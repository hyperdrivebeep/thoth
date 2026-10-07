import { describe, expect, it } from "vitest";
import { eulreul, eunneun, finalHasBatchim, gwawa, iga } from "./koreanParticles";

describe("finalHasBatchim", () => {
  it("reads Hangul by its last syllable", () => {
    expect(finalHasBatchim("조건")).toBe(true);
    expect(finalHasBatchim("질문")).toBe(true);
    expect(finalHasBatchim("가설")).toBe(true);
    expect(finalHasBatchim("원문")).toBe(true);
    expect(finalHasBatchim("사이")).toBe(false);
    expect(finalHasBatchim("금지")).toBe(false);
  });

  it("reads a digit by how it is spoken", () => {
    const spoken = [true, true, false, true, false, false, true, true, true, false];
    spoken.forEach((expected, digit) => expect(finalHasBatchim(String(digit)), String(digit)).toBe(expected));
    // 십, 백, 천 and 만 all carry a final consonant, and so does 영.
    for (const value of ["10", "100", "1000", "10000"]) expect(finalHasBatchim(value), value).toBe(true);
    expect(finalHasBatchim("12")).toBe(false);
    expect(finalHasBatchim("A3")).toBe(true);
    expect(finalHasBatchim("p.6")).toBe(true);
    expect(finalHasBatchim("70.6")).toBe(true);
  });

  it("reads a Latin letter by its name and ignores case", () => {
    for (const letter of ["l", "m", "n", "r", "L", "M", "N", "R"]) expect(finalHasBatchim(letter), letter).toBe(true);
    for (const letter of ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "o", "p", "q", "s", "t", "u", "v", "w", "x", "y", "z"]) {
      expect(finalHasBatchim(letter), letter).toBe(false);
      expect(finalHasBatchim(letter.toUpperCase()), letter.toUpperCase()).toBe(false);
    }
    expect(finalHasBatchim("GNC")).toBe(false);
    expect(finalHasBatchim("ABC")).toBe(false);
    expect(finalHasBatchim("SNR")).toBe(true);
    expect(finalHasBatchim("R")).toBe(true);
    // A lowercase word that ends in r is read as a word (레이더, 필터), not as the letter name 알.
    expect(finalHasBatchim("radar")).toBe(false);
    expect(finalHasBatchim("filter")).toBe(false);
    expect(finalHasBatchim("r")).toBe(true);
    expect(finalHasBatchim("Sr")).toBe(false);
    expect(finalHasBatchim("radar)")).toBe(false);
    expect(eulreul("radar")).toBe("radar를");
    expect(eulreul("filter")).toBe("filter를");
    expect(eulreul("SNR")).toBe("SNR을");
  });

  it("looks past closing brackets, quotes, spaces and full stops", () => {
    expect(finalHasBatchim("표 3)")).toBe(true);
    expect(finalHasBatchim("「인력 공백」")).toBe(true);
    expect(finalHasBatchim("「일정만 늘리면 해결된다」")).toBe(false);
    expect(finalHasBatchim('"조건"')).toBe(true);
    expect(finalHasBatchim("GNC. ")).toBe(false);
    expect(finalHasBatchim("p.6.")).toBe(true);
  });

  it("cannot tell from symbols alone, an empty text or other scripts", () => {
    expect(finalHasBatchim("")).toBeNull();
    expect(finalHasBatchim("   ")).toBeNull();
    expect(finalHasBatchim("()")).toBeNull();
    expect(finalHasBatchim("…")).toBeNull();
    expect(finalHasBatchim("50%")).toBeNull();
  });
});

describe("the four particle pairs share that reading", () => {
  it("picks one form when the ending is known", () => {
    expect(eulreul("표 3")).toBe("표 3을");
    expect(eulreul("조건")).toBe("조건을");
    expect(eulreul("GNC")).toBe("GNC를");
    expect(iga("A3")).toBe("A3이");
    expect(iga("GNC")).toBe("GNC가");
    expect(gwawa("A3")).toBe("A3과");
    expect(gwawa("GNC")).toBe("GNC와");
    expect(gwawa("p.8 GNC")).toBe("p.8 GNC와");
    expect(eunneun("「인력 공백」")).toBe("「인력 공백」은");
    expect(eunneun("「일정만 늘리면 해결된다」")).toBe("「일정만 늘리면 해결된다」는");
  });

  it("writes both forms together when it cannot tell", () => {
    expect(eulreul("50%")).toBe("50%을(를)");
    expect(iga("()")).toBe("()이(가)");
    expect(gwawa("…")).toBe("…과(와)");
    expect(eunneun("%")).toBe("%은(는)");
  });
});
