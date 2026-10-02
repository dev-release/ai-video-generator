"""A drawn character for fake mode: frame, animation, lips to OUR audio — by code, $0.

It mirrors the real chain (frame with a face -> silent clip -> lipsync to the shot audio ->
mouth measurement on the final video), so a free run exercises the same nodes and gates. Look
and scene come from the frame prompt (look, visual_prompt, style).
The face geometry is fixed in relative frame coordinates, so fake lipsync and the fake mouth
tracker find the mouth where it was drawn, with no detector and no network.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pipeline.media import audio_envelope, probe, read_frames, write_frames

W, H, FPS = 1080, 1920, 30
# Relative coordinates on a 9:16 frame (x: share of width, y: share of height).
MOUTH = (0.5, 0.513)
MOUTH_HW = 0.06  # mouth half-width, share of frame width
EYES = ((0.421, 0.417), (0.579, 0.417))
EYE_R = (0.040, 0.013)
MOUTH_INSIDE = (60, 18, 28)  # mouth cavity color
SKIN_PROBE = (0.5, 0.49)  # between nose and mouth: always skin
PUSH_IN = 0.04  # slow camera push-in over a clip, centered on the mouth (lips stay in place)

SKIN = [(255, 219, 190), (241, 194, 160), (224, 172, 130), (198, 134, 94), (141, 85, 54)]
HAIR = {
    "black": (30, 25, 25), "dark": (55, 40, 32), "brown": (110, 70, 40),
    "brunette": (90, 55, 35), "blonde": (225, 190, 120), "blond": (225, 190, 120),
    "red": (170, 70, 35), "ginger": (190, 90, 40), "auburn": (140, 55, 35),
    "grey": (165, 165, 170), "gray": (165, 165, 170), "silver": (200, 200, 205),
    "white": (230, 230, 230),
}  # fmt: skip
CLOTH = {
    "grey": (128, 128, 135), "gray": (128, 128, 135), "black": (35, 35, 40),
    "white": (235, 235, 235), "red": (180, 40, 45), "blue": (50, 80, 160),
    "navy": (30, 40, 80), "green": (50, 120, 70), "yellow": (230, 190, 60),
    "brown": (120, 80, 50), "pink": (220, 130, 160), "purple": (110, 60, 140),
    "beige": (210, 190, 160), "orange": (220, 120, 40), "denim": (70, 100, 140),
}  # fmt: skip
GARMENT = r"sweater|shirt|jacket|coat|dress|hoodie|suit|blouse|top|uniform|cardigan|t-shirt"
MOODS = (
    (r"night|dark|moody|noir|midnight|shadow", ((18, 22, 48), (62, 48, 82))),
    (r"sunset|candle|golden|warm|kitchen|fire", ((246, 176, 108), (112, 58, 48))),
    (r"rain|storm|grey|gray|fog|winter", ((92, 102, 118), (38, 44, 60))),
    (r"forest|park|garden|field|tree", ((74, 124, 84), (24, 50, 36))),
    (r"office|hospital|lab|elevator|corridor", ((200, 208, 214), (116, 126, 138))),
    (r"beach|sea|ocean|sky|summer", ((120, 190, 230), (230, 210, 170))),
)
FEMALE = (
    r"\b(woman|women|girl|girls|girlfriend|she|her|female|lady|mother|mom|grandma|wife|"
    r"sister|sisters|daughter|queen)\b"
)
MALE = (
    r"\b(man|men|boy|boys|boyfriend|he|his|him|male|guy|father|dad|grandpa|husband|"
    r"brother|brothers|son|king)\b"
)


def split_prompt(prompt: str) -> tuple[str, str, str]:
    """(framing, look, scene) from the scene prompt of `video.scene_prompt`:
    "<style>. <camera>, vertical 9:16. <look>. <visual>…". The character comes only from look
    (same in every shot), the background from the rest ("grey hair" is not a grey background)."""
    prompt = prompt.split(" The character speaks")[0]  # the line is not part of the scene
    cam = re.search(r"([^.]*), vertical 9:16", prompt)
    m = re.search(r"vertical 9:16\. (.+?)\. ", prompt)
    look = m.group(1) if m else prompt
    return (cam.group(1).strip() if cam else ""), look, prompt.replace(look, "")


def framing_zoom(camera: str) -> float:
    """Framing: how many times closer than a medium close-up (centered on the mouth)."""
    c = camera.lower()
    if "medium" in c or "close" not in c:
        return 1.0
    return 1.6 if "extreme" in c else 1.3


def is_male(text: str) -> bool:
    """Gender by the first gendered word ("an old man … his late wife" -> male).
    No such word -> a female character by default."""
    m = re.search(f"{MALE}|{FEMALE}", text)
    return m is not None and _word(MALE, m.group(0))


def _h(text: str, n: int) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % n


def _word(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


@dataclass(frozen=True)
class Look:
    female: bool
    old: bool
    long_hair: bool
    curly: bool
    beard: bool
    glasses: bool
    skin: tuple[int, int, int]
    hair: tuple[int, int, int]
    cloth: tuple[int, int, int]
    iris: tuple[int, int, int]
    bg: tuple[tuple[int, int, int], tuple[int, int, int]]
    light_x: float = 0.2  # window position: another seed gives other light


def parse_look(look: str, scene: str, seed: int = 0) -> Look:
    """Look and scene from the prompt text, deterministic: same look -> same character."""
    lk, sc = look.lower(), scene.lower()
    female = not is_male(lk if _word(f"{FEMALE}|{MALE}", lk) else sc)
    old = _word(r"\b(old|elderly|grandma|grandpa|grandmother|grandfather|sixties|seventies)\b", lk)
    hair_word = next((w for w in HAIR if _word(rf"\b{w}\b[\w\s,-]*\bhair\b", lk)), None)
    hair = HAIR[hair_word] if hair_word else (HAIR["grey"] if old else HAIR[list(HAIR)[_h(lk, 6)]])
    cloth_word = None
    for m in re.finditer(rf"((?:\w+[\s-]+){{0,3}})(?:{GARMENT})\b", lk):
        cloth_word = next((w for w in m.group(1).split() if w in CLOTH), cloth_word)
    cloth = CLOTH[cloth_word] if cloth_word else list(CLOTH.values())[_h(lk + "c", len(CLOTH))]
    bg = next((c for p, c in MOODS if _word(p, sc)), None)
    if bg is None:
        a, b = hashlib.sha256(sc.encode()).digest()[:3], hashlib.sha256(sc[::-1].encode()).digest()
        bg = (tuple(a), tuple(b[:3]))
    return Look(
        female=female,
        old=old,
        long_hair=_word(r"\blong\b", lk) or (female and not _word(r"\bshort\b", lk)),
        curly=_word(r"curl|wavy|afro", lk),
        beard=not female and _word(r"beard|stubble", lk),
        glasses=_word(r"glasses|spectacles", lk),
        skin=SKIN[_h(lk + "skin", len(SKIN))],
        hair=hair,
        cloth=cloth,
        iris=[(70, 110, 60), (60, 90, 150), (90, 60, 40), (50, 40, 30)][_h(lk + "eye", 4)],
        bg=bg,  # type: ignore[arg-type]
        light_x=0.2 + 0.6 * _h(f"light{seed}", 1000) / 1000 if seed else 0.2,
    )


def _px(x: float, y: float, w: int, h: int) -> tuple[int, int]:
    return round(x * w), round(y * h)


def draw_portrait(look: Look, w: int = W, h: int = H, zoom: float = 1.0) -> np.ndarray:
    """Portrait: RGB uint8 (h, w, 3); zoom is the framing around the mouth (1 = medium close-up).
    The mouth is closed; lipsync opens it."""
    import cv2

    s = w / W
    aa = cv2.LINE_AA
    top, bottom = np.array(look.bg[0], float), np.array(look.bg[1], float)
    grad = np.linspace(0, 1, h)[:, None, None]
    img = (top * (1 - grad) + bottom * grad).repeat(w, axis=1).astype(np.float32)
    # Soft window light from the top left.
    yy, xx = np.mgrid[0:h, 0:w]
    glow = np.exp(
        -(((xx - look.light_x * w) / (0.6 * w)) ** 2 + ((yy - 0.15 * h) / (0.45 * h)) ** 2)
    )
    light = 70 * (1 - 0.7 * top.mean() / 255)  # do not blow out a light background
    img = np.clip(img + light * glow[..., None], 0, 255).astype(np.uint8)

    mx, my = MOUTH[0] * W, MOUTH[1] * H

    def P(x: float, y: float) -> tuple[int, int]:  # 1080x1920 layout -> frame, zoom around mouth
        return round((mx + zoom * (x - mx)) * s), round((my + zoom * (y - my)) * h / H)

    def S(v: float) -> int:
        return max(1, round(v * zoom * s))

    skin, hair, cloth = look.skin, look.hair, look.cloth
    shade = tuple(int(c * 0.82) for c in skin)
    if look.long_hair:  # hair behind the shoulders
        cv2.ellipse(img, P(540, 900), (S(330), S(470)), 0, 0, 360, hair, -1, aa)
    cv2.ellipse(img, P(540, 1820), (S(480), S(560)), 0, 0, 360, cloth, -1, aa)
    cv2.rectangle(img, P(470, 1060), P(610, 1300), shade, -1, aa)
    cv2.ellipse(img, P(540, 1290), (S(95), S(55)), 0, 0, 180, shade, -1, aa)
    for x in (308, 772):  # ears
        cv2.ellipse(img, P(x, 830), (S(34), S(62)), 0, 0, 360, shade, -1, aa)
    cv2.ellipse(img, P(540, 820), (S(232), S(300)), 0, 0, 360, skin, -1, aa)
    if look.beard:
        cv2.ellipse(img, P(540, 960), (S(215), S(165)), 0, 0, 180, hair, -1, aa)
        cv2.ellipse(img, P(540, 985), (S(105), S(55)), 0, 0, 360, skin, -1, aa)
    # Fringe / hairstyle on top.
    cv2.ellipse(img, P(540, 690), (S(250), S(215)), 0, 180, 360, hair, -1, aa)
    cv2.ellipse(img, P(540, 600), (S(245), S(140)), 0, 0, 360, hair, -1, aa)
    if look.curly:
        for i in range(11):
            a = math.pi * (1.05 + 0.9 * i / 10)
            cv2.circle(
                img, P(540 + 250 * math.cos(a), 690 + 215 * math.sin(a)), S(52), hair, -1, aa
            )
    for ex, ey in EYES:  # brows
        cx, cy = ex * W, ey * H
        cv2.ellipse(img, P(cx, cy - 52), (S(52), S(18)), 0, 200, 340, hair, S(11), aa)
    for ex, ey in EYES:  # eyes
        cx, cy = ex * W, ey * H
        cv2.ellipse(img, P(cx, cy), (S(44), S(25)), 0, 0, 360, (250, 250, 250), -1, aa)
        cv2.circle(img, P(cx, cy), S(21), look.iris, -1, aa)
        cv2.circle(img, P(cx, cy), S(10), (20, 20, 25), -1, aa)
        cv2.circle(img, P(cx + 7, cy - 8), S(6), (255, 255, 255), -1, aa)
        cv2.ellipse(img, P(cx, cy), (S(44), S(25)), 0, 180, 360, (40, 30, 30), S(4), aa)
    if look.glasses:
        for ex, ey in EYES:
            cv2.ellipse(img, P(ex * W, ey * H), (S(66), S(50)), 0, 0, 360, (40, 40, 45), S(7), aa)
        cv2.line(img, P(515, 800), P(565, 800), (40, 40, 45), S(7), aa)
    cv2.ellipse(img, P(540, 880), (S(22), S(42)), 0, 30, 150, shade, S(6), aa)  # nose
    blush = img.copy()
    for x in (420, 660):
        cv2.ellipse(blush, P(x, 935), (S(55), S(32)), 0, 0, 360, (235, 130, 130), -1, aa)
    img = cv2.addWeighted(blush, 0.18, img, 0.82, 0)
    if look.old:
        for x in (455, 625):
            cv2.ellipse(img, P(x, 845), (S(40), S(14)), 0, 20, 160, shade, S(3), aa)
    draw_mouth(img, 0.0, zoom)  # the same lips lipsync draws: approval frame == video
    return img


def lip_color(skin: tuple[int, ...]) -> tuple[int, int, int]:
    return (min(255, int(skin[0] * 0.8)), int(skin[1] * 0.45), int(skin[2] * 0.5))


def _around_mouth(pt: tuple[float, float], zoom: float, w: int, h: int) -> tuple[int, int]:
    """Relative face point -> frame pixels at zoom (centered on the mouth)."""
    return _px(MOUTH[0] + zoom * (pt[0] - MOUTH[0]), MOUTH[1] + zoom * (pt[1] - MOUTH[1]), w, h)


def mouth_scale(img: np.ndarray) -> float:
    """Framing from the closed-lip line in the mouth row: its length is 2*MOUTH_HW*zoom. Fake
    stages read the framing from the frame itself, like real models, with no extra parameters."""
    h, w = img.shape[:2]
    cx, cy = _px(*MOUTH, w, h)
    lum = img[:, :, :3].astype(float) @ np.array([0.299, 0.587, 0.114])
    ref_y = cy - round(0.6 * MOUTH_HW * w)  # above the lips: skin (also inside a beard)
    skin = float(np.median(lum[ref_y - 2 : ref_y + 3, cx - 2 : cx + 3]))
    dark = lum[cy] < skin - 25
    if not dark[cx]:
        return 1.0  # mouth not closed or not drawn: no estimate
    left = right = cx
    while left > 0 and dark[left - 1]:
        left -= 1
    while right < w - 1 and dark[right + 1]:
        right += 1
    return float(np.clip((right - left) / 2 / (MOUTH_HW * w), 0.8, 2.0))


def draw_mouth(img: np.ndarray, openness: float, zoom: float = 1.0) -> None:
    """Mouth at a fixed point over the frame: 0 = closed (lip line), 1 = wide open."""
    import cv2

    h, w = img.shape[:2]
    aa = cv2.LINE_AA
    cx, cy = _px(*MOUTH, w, h)
    hw = MOUTH_HW * w * zoom
    sx, sy = _around_mouth(SKIN_PROBE, zoom, w, h)
    skin = tuple(int(c) for c in np.median(img[sy - 3 : sy + 4, sx - 3 : sx + 4].reshape(-1, 3), 0))
    lips = lip_color(skin)
    # Paint the previous mouth over with skin: a frame after the video model has closed lips.
    cv2.ellipse(img, (cx, cy), (round(hw * 1.25), round(hw * 0.75)), 0, 0, 360, skin, -1, aa)
    if openness < 0.06:
        cv2.ellipse(img, (cx, cy), (round(hw), round(hw * 0.2)), 0, 0, 360, lips, -1, aa)
        cv2.line(
            img,
            (round(cx - hw), cy),
            (round(cx + hw), cy),
            (90, 35, 40),
            max(2, round(w * zoom / 360)),
            aa,
        )
        return
    rx, ry = round(hw * (1 - 0.22 * openness)), round(hw * (0.14 + 0.62 * openness))
    rim = round(w * zoom / 180)
    cv2.ellipse(img, (cx, cy), (rx + rim, ry + rim), 0, 0, 360, lips, -1, aa)
    cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, MOUTH_INSIDE, -1, aa)
    if openness > 0.25:  # upper teeth and tongue
        cv2.ellipse(img, (cx, cy - round(ry * 0.72)), (round(rx * 0.75), round(ry * 0.3)),
                    0, 0, 360, (245, 245, 240), -1, aa)  # fmt: skip
        cv2.ellipse(img, (cx, cy + round(ry * 0.65)), (round(rx * 0.6), round(ry * 0.35)),
                    0, 0, 360, (200, 90, 100), -1, aa)  # fmt: skip


def _blink(t: float) -> bool:
    # Deterministic blinks: about every 3.1 s for 0.13 s, like a real person.
    return (t - 0.9) % 3.1 < 0.13 and t > 0.9


def _close_eyes(img: np.ndarray, zoom: float) -> None:
    import cv2

    h, w = img.shape[:2]
    sx, sy = _around_mouth(SKIN_PROBE, zoom, w, h)
    skin = tuple(int(c) for c in img[sy, sx])
    for eye in EYES:
        c = _around_mouth(eye, zoom, w, h)
        ax = (round(EYE_R[0] * w * 1.15 * zoom), round(EYE_R[1] * h * 1.3 * zoom))
        cv2.ellipse(img, c, ax, 0, 0, 360, skin, -1, cv2.LINE_AA)
        cv2.ellipse(img, c, (ax[0], ax[1] // 3), 0, 0, 180, (40, 30, 30), max(2, w // 270))


def animate(base: np.ndarray, duration_s: float, out: Path, end_zoom: float = 1 + PUSH_IN) -> Path:
    """Animate a frame: blinks and a camera move to the mouth (zoom 1 -> end_zoom, smooth). Lips
    stay closed (lipsync moves them). The first clip frame = base."""
    import cv2

    h, w = base.shape[:2]
    cx, cy = _px(*MOUTH, w, h)
    zoom = mouth_scale(base)
    n = max(1, round(duration_s * FPS))

    def frames():
        for i in range(n):
            f = base.copy()
            if _blink(i / FPS):
                _close_eyes(f, zoom)
            x = i / max(n - 1, 1)
            z = 1 + (end_zoom - 1) * x * x * (3 - 2 * x)  # smoothstep: the camera eases in and out
            m = np.float32([[z, 0, (1 - z) * cx], [0, z, (1 - z) * cy]])
            yield cv2.warpAffine(
                f, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT
            )

    return write_frames(frames(), w, h, FPS, out)


def text_to_video(prompt: str, duration_s: float, out: Path, seed: int = 0) -> Path:
    """ "Text model" (shot 1): character and scene from the scene prompt, framing from camera."""
    camera, look, scene = split_prompt(prompt)
    img = draw_portrait(parse_look(look, scene, seed), zoom=framing_zoom(camera))
    return animate(img, duration_s, out)


def image_to_video(image: Path, prompt: str, duration_s: float, out: Path) -> Path:
    """ "Frame model" (shot k): starts on the cut frame, the camera moves to "Camera: …"."""
    import cv2

    bgr = cv2.imread(str(image))
    if bgr is None:
        raise ValueError(f"cut frame is not readable: {image}")
    base = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    cam = re.search(r"Camera: ([^.]*)", prompt)
    target = framing_zoom(cam.group(1) if cam else "")
    return animate(base, duration_s, out, max(1 + PUSH_IN, target / mouth_scale(base)))


def openness(wav: Path, fps: int = FPS) -> np.ndarray:
    """Mouth openness per frame = RMS envelope of our audio (0..1). No smoothing or saturation:
    the mouth moves on every syllable, like real speech (spec sync.md)."""
    return np.clip(np.asarray(audio_envelope(wav, fps), dtype=float), 0, 1)


def lipsync(clip: Path, audio: Path, out: Path) -> Path:
    """ "Lipsync model": the mouth in every clip frame opens to the shot audio. Writes no audio."""
    o = openness(audio)
    # Framing from the clip frames (closed-lip line), fitted with a line: the push-in is smooth.
    zs = [mouth_scale(f) for f in read_frames(clip, FPS)]
    fit = (
        np.polyval(np.polyfit(np.arange(len(zs)), zs, 1), np.arange(len(zs))) if len(zs) > 1 else zs
    )

    def frames():
        for i, f in enumerate(read_frames(clip, FPS)):
            draw_mouth(f, float(o[i]) if i < len(o) else 0.0, float(fit[min(i, len(fit) - 1)]))
            yield f

    p = probe(clip)
    return write_frames(frames(), p.width or W, p.height or H, FPS, out)


def mouth_openness(frames: list[bytes], width: int, height: int) -> list[float | None]:
    """Mouth openness per frame: share of mouth cavity in the area where draw_mouth paints it
    (framing from the first frame). The same measure as the real tracker."""
    from pipeline.providers.face import normalized

    cx, cy = _px(*MOUTH, width, height)
    first = np.frombuffer(frames[0], dtype=np.uint8).reshape(height, width, 3) if frames else None
    hw = MOUTH_HW * width * (mouth_scale(first) if first is not None else 1.0)
    x0, x1 = round(cx - 1.2 * hw), round(cx + 1.2 * hw)
    y0, y1 = round(cy - 0.8 * hw), round(cy + 0.8 * hw)
    inside = np.array(MOUTH_INSIDE, dtype=int)
    raw: list[float | None] = []
    for frame in frames:
        rgb = np.frombuffer(frame, dtype=np.uint8).reshape(height, width, 3)
        roi = rgb[y0:y1, x0:x1].astype(int)
        raw.append(float((np.abs(roi - inside).sum(axis=2) < 60).mean()))
    return normalized(raw)
