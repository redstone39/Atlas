import type { Document } from "@hocuspocus/server";
import type { AuthorizationResult, JsonObject } from "./types.js";

const MAX_TIMER_SECONDS = Math.floor(2_147_483_647 / 1_000);

export interface TimerDriver {
  set(callback: () => void, milliseconds: number): NodeJS.Timeout;
  clear(timer: NodeJS.Timeout): void;
}

export const systemTimer: TimerDriver = {
  set: (callback, milliseconds) => setTimeout(callback, milliseconds),
  clear: timer => clearTimeout(timer),
};

export class PromiseMutex {
  private tail: Promise<void> = Promise.resolve();
  private users = 0;

  async acquire(): Promise<() => void> {
    this.users += 1;
    const { promise: gate, resolve } = Promise.withResolvers<void>();
    const previous = this.tail;
    this.tail = previous.then(() => gate);
    await previous;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.users -= 1;
      resolve();
    };
  }

  async run<T>(operation: () => Promise<T>): Promise<T> {
    const release = await this.acquire();
    try {
      return await operation();
    } finally {
      release();
    }
  }
  get isIdle(): boolean {
    return this.users === 0;
  }
}

export class RoomState {
  document: Document | null = null;
  context: AuthorizationResult;
  revisionHead: number;
  savepointHead: number;
  dirty = false;
  currentBody: JsonObject;
  checkpointBody: JsonObject;
  private timer: NodeJS.Timeout | null = null;
  private timerGeneration = 0;

  constructor(
    readonly roomName: string,
    readonly noteId: string,
    readonly mutex: PromiseMutex,
    context: AuthorizationResult,
    currentBody: JsonObject,
    checkpointBody: JsonObject,
    private readonly timerDriver: TimerDriver,
  ) {
    this.context = context;
    this.revisionHead = context.accepted_update_head;
    this.savepointHead = context.savepoint_head;
    this.currentBody = currentBody;
    this.checkpointBody = checkpointBody;
  }

  schedule(intervalSeconds: number, callback: () => Promise<void>): void {
    this.cancelTimer();
    const generation = this.timerGeneration;
    const wait = (remainingSeconds: number): void => {
      const chunk = Math.min(remainingSeconds, MAX_TIMER_SECONDS);
      this.timer = this.timerDriver.set(() => {
        if (generation !== this.timerGeneration) return;
        const remaining = remainingSeconds - chunk;
        if (remaining > 0) {
          wait(remaining);
          return;
        }
        void callback();
      }, chunk * 1_000);
    };
    wait(intervalSeconds);
  }

  cancelTimer(): void {
    this.timerGeneration += 1;
    if (this.timer) this.timerDriver.clear(this.timer);
    this.timer = null;
  }
}
