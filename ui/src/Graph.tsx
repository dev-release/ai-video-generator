import {
  Background,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { usd, type NodeView } from './api'

// What a stage does and which capability (script/tts/…) is behind it. The concrete model comes
// from the run's models.json: the UI knows no vendors.
const META: Record<string, { title: string; who: string; cap?: string }> = {
  script: { title: 'Script', who: 'structured output', cap: 'script' },
  voice: { title: 'Voice', who: 'TTS from the exact line', cap: 'tts' },
  voice_check: { title: 'Audio gate', who: 'shot ASR → WER == 0, before video', cap: 'asr' },
  approve_script: { title: 'Approve script', who: 'human, optional · exact video prompts' },
  video: { title: 'Video', who: 'shot 1 from text, next from the cut frame; silent', cap: 'video' },
  keyframe: { title: 'Cut frame', who: 'end of a shot → start of the next · $0' },
  approve: { title: 'Approve between shots', who: 'human, optional' },
  lipsync: { title: 'Lipsync', who: 'lips to OUR audio, video only', cap: 'lipsync' },
  assemble: { title: 'Edit', who: 'ffmpeg · −14 LUFS' },
  verify: { title: 'Verbatim gate', who: 'final ASR → WER == 0', cap: 'asr' },
  sync_check: { title: 'Lip sync', who: 'mouth opening vs our audio', cap: 'face' },
}

type StepData = { view: NodeView; selected: boolean; model?: string }

function Step({ data }: NodeProps<Node<StepData>>) {
  const { view, selected, model } = data
  const meta = META[view.name]
  return (
    <div className={`step st-${view.status} ${selected ? 'sel' : ''}`}>
      <Handle type="target" position={Position.Top} />
      <div className="step-head">
        <span className="dot" />
        <b>{meta.title}</b>
        {view.runs > 1 && <span className="runs-x">×{view.runs}</span>}
      </div>
      <div className="step-who">
        {meta.who}
        {model && <span className="step-model"> · {model}</span>}
      </div>
      <div className="step-meta">
        {view.duration_s > 0 && <span>{view.duration_s.toFixed(1)} s</span>}
        {view.cost_usd > 0 && <span>{usd(view.cost_usd)}</span>}
      </div>
      <Handle type="source" position={Position.Bottom} />
      <Handle type="source" id="loop" position={Position.Right} />
      <Handle type="target" id="loop-in" position={Position.Right} />
    </div>
  )
}

const nodeTypes = { step: Step }
const GAP = 108

const edge = (source: string, target: string, extra: Partial<Edge> = {}): Edge => ({
  id: `${source}-${target}${extra.sourceHandle ?? ''}`,
  source,
  target,
  markerEnd: { type: MarkerType.ArrowClosed },
  ...extra,
})

export function Graph({
  nodes,
  models,
  selected,
  onSelect,
}: {
  nodes: NodeView[]
  models: Record<string, string>
  selected: string
  onSelect: (name: string) => void
}) {
  const byName = Object.fromEntries(nodes.map((n) => [n.name, n]))
  const flowNodes: Node<StepData>[] = nodes.map((n, i) => ({
    id: n.name,
    type: 'step',
    position: { x: 0, y: i * GAP },
    data: {
      view: n,
      selected: n.name === selected,
      model: META[n.name]?.cap ? models[META[n.name].cap as string] : undefined,
    },
  }))
  const order = nodes.map((n) => n.name)
  // Straight edges in order, except approve -> lipsync: after approval the graph returns to video.
  const straight = order
    .slice(1)
    .map((t, i) => [order[i], t] as const)
    .filter(([s, t]) => !(s === 'approve' && t === 'lipsync'))
  const edges: Edge[] = [
    ...straight.map(([s, t]) => edge(s, t, { animated: byName[t]?.status === 'running' })),
    edge('video', 'lipsync', { animated: byName.lipsync?.status === 'running', type: 'smoothstep' }),
    // Graph loops: audio gate -> new voice take (before video); rewrite the script from approval;
    // after the cut frame approval -> the next shot from that frame. The final gate has no loop:
    // paid work is never regenerated automatically.
    edge('voice_check', 'voice', {
      sourceHandle: 'loop',
      targetHandle: 'loop-in',
      type: 'smoothstep',
      label: 'new take',
      className: 'loop',
    }),
    edge('approve_script', 'script', {
      sourceHandle: 'loop',
      targetHandle: 'loop-in',
      type: 'smoothstep',
      label: 'rewrite',
      className: 'loop',
    }),
    edge('approve', 'video', {
      sourceHandle: 'loop',
      targetHandle: 'loop-in',
      type: 'smoothstep',
      label: 'next shot from frame',
      className: 'loop',
    }),
  ]

  return (
    <div className="graph">
      <ReactFlow
        nodes={flowNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onSelect(n.id)}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} size={1} />
      </ReactFlow>
    </div>
  )
}
