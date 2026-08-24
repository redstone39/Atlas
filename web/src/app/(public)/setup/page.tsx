import { Suspense } from "react";

import { SetupScreen } from "./screen";

export default function SetupPage() {
  return (
    <Suspense>
      <SetupScreen />
    </Suspense>
  );
}
