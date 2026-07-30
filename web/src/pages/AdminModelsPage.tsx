import {
  ModelRoutingFeature,
  type ModelRoutingFeatureProps,
} from "../features/model-routing/index";

export function AdminModelsPage(props: ModelRoutingFeatureProps) {
  return <ModelRoutingFeature {...props} />;
}
