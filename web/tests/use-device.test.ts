import test from "node:test";
import assert from "node:assert/strict";
import {
  DEVICE_BREAKPOINTS,
  classifyDeviceWidth,
} from "../hooks/useDevice";

test("useDevice policy keeps the three viewport classes at the Tailwind edges", () => {
  assert.deepEqual(DEVICE_BREAKPOINTS, { tablet: 768, desktop: 1024 });
  assert.equal(classifyDeviceWidth(320), "mobile");
  assert.equal(classifyDeviceWidth(767), "mobile");
  assert.equal(classifyDeviceWidth(768), "tablet");
  assert.equal(classifyDeviceWidth(1023), "tablet");
  assert.equal(classifyDeviceWidth(1024), "desktop");
  assert.equal(classifyDeviceWidth(1280), "desktop");
});

test("useDevice policy treats narrow and portrait-tablet widths as compact", () => {
  for (const width of [320, 390, 767, 768, 820, 1023]) {
    assert.notEqual(classifyDeviceWidth(width), "desktop", `${width}px`);
  }
});
