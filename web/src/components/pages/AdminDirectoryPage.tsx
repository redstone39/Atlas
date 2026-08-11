import { DirectoryAdministrationFeature } from "../../features/directory-administration";

export function AdminDirectoryPage({
  onNotice,
  onRefresh,
}: {
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  return <DirectoryAdministrationFeature onNotice={onNotice} onRefresh={onRefresh} />;
}
