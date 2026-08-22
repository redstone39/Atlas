import { NotesCarrier } from "./carrier.js";
import { readConfig } from "./config.js";
import { installProcessShutdown } from "./process-shutdown.js";

const carrier = new NotesCarrier(readConfig());
const processShutdown = installProcessShutdown(carrier);

try {
  await carrier.listen();
} catch (error) {
  processShutdown.remove();
  await processShutdown.shutdown();
  throw error;
}
