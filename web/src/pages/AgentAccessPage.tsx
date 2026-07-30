import {
  AgentAccessFeature,
  type AgentAccessFeatureProps,
} from "../features/agent-access/index";

export function AgentAccessPage(props: AgentAccessFeatureProps) {
  return <AgentAccessFeature {...props} />;
}
