import { NotesCarrier } from "./carrier.js";
import { readConfig } from "./config.js";

const carrier = new NotesCarrier(readConfig());
await carrier.listen();
