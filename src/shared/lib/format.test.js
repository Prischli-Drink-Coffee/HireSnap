import { formatSimilarity } from "shared/lib/format";

// formatSimilarity is the only thing between a raw model score and the number a
// reader sees, and it is called during render - a throw here blanks a table cell,
// not just a page. So the contract under test is "always returns a string", and
// the interesting cases are the ones that are NOT a finite number.
describe("formatSimilarity", () => {
  test("prints a number with exactly three decimals", () => {
    expect(formatSimilarity(0.5)).toBe("0.500");
    expect(formatSimilarity(0.1234)).toBe("0.123");
  });

  // The backend serialises "distance" out of a DECIMAL column, so it arrives as a
  // string often enough that treating it as a number is the normal path, not an
  // edge case. Number() before toFixed is the whole reason the helper exists.
  test("accepts a numeric string, which is how a DECIMAL column arrives", () => {
    expect(formatSimilarity("0.1234")).toBe("0.123");
    expect(formatSimilarity("0")).toBe("0.000");
    expect(formatSimilarity("1")).toBe("1.000");
  });

  test("rounds to three decimals", () => {
    // 0.12349 is the interesting one: it must print "0.123" by rounding, not by
    // the tolerance of a truncation, and 0.0005 must round UP to "0.001" - a
    // truncating implementation would print "0.000" and hide a real score.
    expect(formatSimilarity(0.12349)).toBe("0.123");
    expect(formatSimilarity(0.1236)).toBe("0.124");
    expect(formatSimilarity(0.0005)).toBe("0.001");
    expect(formatSimilarity(0.9999)).toBe("1.000");
  });

  test("keeps the two boundary values", () => {
    expect(formatSimilarity(0)).toBe("0.000");
    expect(formatSimilarity(1)).toBe("1.000");
  });

  // The em-dash is a deliberate UI decision recorded in the module, not an
  // accident: "no comparable score" must not be rendered as 0.000, which would
  // claim a perfect dissimilarity the data does not support.
  test("renders the em-dash placeholder for values that are not a finite number", () => {
    expect(formatSimilarity(undefined)).toBe("—");
    expect(formatSimilarity(NaN)).toBe("—");
    expect(formatSimilarity(Infinity)).toBe("—");
    expect(formatSimilarity(-Infinity)).toBe("—");
    expect(formatSimilarity("not a number")).toBe("—");
  });

  // REGRESSION CASE, and the reason the guard exists. Before it, null and ""
  // reached Number(), which folds both to 0 - so an absent score printed
  // "0.000", the same string a genuinely dissimilar candidate prints, and the
  // table could not tell "no score" from "zero similarity". The helper now
  // rejects the absent values before the coercion; these assertions are what
  // keep that guard from being optimised away later.
  test("renders the em-dash for an absent score, not 0.000", () => {
    expect(formatSimilarity(null)).toBe("—");
    expect(formatSimilarity("")).toBe("—");
  });

  test("renders the em-dash for a blank or whitespace-only string", () => {
    // The trim is part of the guard: a DECIMAL column that serialised to "   "
    // is absent, not zero, and Number("   ") would have said 0.
    expect(formatSimilarity("   ")).toBe("—");
    expect(formatSimilarity("\t")).toBe("—");
  });

  test("still treats the numeric string \"0\" as a real score of zero", () => {
    // The other half of the guard: only an EMPTY string is absent. "0" is a
    // measurement and must keep printing 0.000, or the fix would have replaced
    // one wrong cell with another.
    expect(formatSimilarity("0")).toBe("0.000");
    expect(formatSimilarity(0)).toBe("0.000");
    expect(formatSimilarity(" 0 ")).toBe("0.000");
  });
});
