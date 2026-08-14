import assert from "node:assert/strict";
import { test } from "node:test";
import { readConfig } from "../src/config.js";
import { PromiseMutex, RoomState, type TimerDriver } from "../src/room-state.js";
import type { AuthorizationResult } from "../src/types.js";

const context: AuthorizationResult = {
  note_id: "note",
  actor_id: "actor",
  room_name: "room",
  connection_token: "token",
  collaboration_epoch: 1,
  read_only: false,
  accepted_update_head: 1,
  savepoint_head: 1,
};

test("configuration requires the private API URL and transport secret with loopback listen default", () => {
  assert.throws(() => readConfig({}), /INTERNAL_URL/);
  assert.throws(() => readConfig({ ATLAS_NOTES_COLLABORATION_INTERNAL_URL: "http://api" }), /INTERNAL_SECRET/);
  assert.throws(() => readConfig({
    ATLAS_NOTES_COLLABORATION_INTERNAL_URL: "http://user:password@api",
    ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET: "secret",
  }), /without credentials/);
  const config = readConfig({
    ATLAS_NOTES_COLLABORATION_INTERNAL_URL: "http://api:8000",
    ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET: "secret",
  });
  assert.equal(config.host, "127.0.0.1");
  assert.equal(config.port, 8015);
});

test("positive intervals above the Node timer maximum are chunked instead of overflowing", () => {
  const delays: number[] = [];
  const callbacks: Array<() => void> = [];
  const timers: TimerDriver = {
    set(callback, milliseconds) {
      callbacks.push(callback);
      delays.push(milliseconds);
      return {} as NodeJS.Timeout;
    },
    clear() {},
  };
  const room = new RoomState("room", "note", new PromiseMutex(), context, {}, {}, timers);
  room.schedule(3_000_000, async () => {});
  assert.equal(delays[0], 2_147_483_000);
  callbacks[0]();
  assert.equal(delays[1], 852_517_000);
});
