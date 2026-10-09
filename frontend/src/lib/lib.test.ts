import { describe, expect, it } from "vitest";
import { citationId, formatDuration, formatSeconds, linkCitations } from "./citations";
import { SseParser } from "./sse";

describe("SseParser", () => {
  it("parses events split across arbitrary chunk boundaries", () => {
    const parser = new SseParser();
    const stream = 'event: delta\ndata: {"text":"Hel"}\n\n: keep-alive\n\nevent: delta\ndata: {"text":"lo"}\n\nevent: done\nda';
    const events = [];
    for (let i = 0; i < stream.length; i += 7) events.push(...parser.push(stream.slice(i, i + 7)));
    events.push(...parser.push('ta: {"ok":true}\n\n'));
    expect(events.map((e) => e.event)).toEqual(["delta", "delta", "done"]);
    expect(JSON.parse(events[0].data).text + JSON.parse(events[1].data).text).toBe("Hello");
  });

  it("handles CRLF and multi-line data", () => {
    const parser = new SseParser();
    const events = parser.push("event: x\r\ndata: a\r\ndata: b\r\n\r\n");
    expect(events).toEqual([{ event: "x", data: "a\nb" }]);
  });

  it("ignores comment-only blocks", () => {
    expect(new SseParser().push(": stream-open\n\n")).toEqual([]);
  });
});

describe("linkCitations", () => {
  it("links markers but not those inside code", () => {
    const out = linkCitations("PMF is measurable [S1][S2]. `[S3]`\n```\n[S4]\n```");
    expect(out).toContain("[S1](#cite-S1)[S2](#cite-S2)");
    expect(out).toContain("`[S3]`");
    expect(out).toContain("[S4]\n```");
  });

  it("does not double-link existing links", () => {
    expect(linkCitations("[S1](#cite-S1)")).toBe("[S1](#cite-S1)");
  });

  it("extracts ids from hrefs", () => {
    expect(citationId("#cite-S12")).toBe("S12");
    expect(citationId("https://example.com")).toBeNull();
  });
});

describe("formatting", () => {
  it("formats timestamps and durations", () => {
    expect(formatSeconds(3192)).toBe("53:12");
    expect(formatSeconds(3725)).toBe("1:02:05");
    expect(formatDuration(850)).toBe("850 ms");
    expect(formatDuration(37825)).toBe("37.8 s");
    expect(formatDuration(95000)).toBe("1m 35s");
  });
});
