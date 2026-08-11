import {
  WorkspaceFeature,
  type WorkspaceFeatureProps,
} from "../../features/workspace/index";

export function WorkspacePage(props: WorkspaceFeatureProps) {
  return <WorkspaceFeature {...props} />;
}
