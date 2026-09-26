import { describe, expect, test } from "bun:test";
import { stateProjectId, upsertEnv } from "./state-key";

describe("upsertEnv", () => {
  test("добавляет ключи, сохраняя остальные строки", () => {
    expect(upsertEnv("SELECTEL_USERNAME=u\nPULUMI_CONFIG_PASSPHRASE=p\n", { AWS_ACCESS_KEY_ID: "AK", AWS_SECRET_ACCESS_KEY: "SK" }))
      .toBe("SELECTEL_USERNAME=u\nPULUMI_CONFIG_PASSPHRASE=p\nAWS_ACCESS_KEY_ID=AK\nAWS_SECRET_ACCESS_KEY=SK\n");
  });
  test("заменяет старые значения, а не дублирует", () => {
    expect(upsertEnv("AWS_ACCESS_KEY_ID=old\nX=1\nAWS_SECRET_ACCESS_KEY=old", { AWS_ACCESS_KEY_ID: "AK", AWS_SECRET_ACCESS_KEY: "SK" }))
      .toBe("X=1\nAWS_ACCESS_KEY_ID=AK\nAWS_SECRET_ACCESS_KEY=SK\n");
  });
  test("CRLF-файл: переводит в LF, не оставляя \\r в значениях", () => {
    expect(upsertEnv("X=1\r\nAWS_ACCESS_KEY_ID=old\r\n", { AWS_ACCESS_KEY_ID: "AK" })).toBe("X=1\nAWS_ACCESS_KEY_ID=AK\n");
  });
  test("пустой файл", () => {
    expect(upsertEnv("", { A: "1" })).toBe("A=1\n");
  });
});

describe("stateProjectId", () => {
  test("берёт id проекта стейта из OS_PROJECT_ID", () => {
    expect(stateProjectId({ OS_PROJECT_ID: "abc123" })).toBe("abc123");
  });
  test("без OS_PROJECT_ID — подсказка про env.sh", () => {
    expect(() => stateProjectId({})).toThrow(/OS_PROJECT_ID.*env\.sh/);
  });
});
