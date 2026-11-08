// Pure presentation helpers, no React and no state.
//
// shared/lib exists for code that is (a) not a hook, (b) not a component and
// (c) needed by more than one feature. It currently holds one function, which is
// the honest size of it: file-per-concern would create helpers nobody calls.

// A similarity score, as the match tables print it.
//
// The backend returns "distance" as a number or, because it is serialised from a
// DECIMAL column, occasionally as a string - Number() before toFixed is what
// keeps a string from rendering "0.1234" as "NaN". Fixed at three decimals
// because more digits than that are noise at this model's resolution, and fewer
// make two close candidates look identical.
//
// An ABSENT score is not a score of zero, and the table has one cell to say so.
// null, undefined and a blank string are rejected before Number() runs, because
// Number(null) and Number("") are both 0 - which prints "0.000", the exact
// string a perfectly dissimilar candidate renders, so the reader could not tell
// "no score" from "zero similarity". (Number(undefined) is NaN and would land on
// the em-dash anyway; it is listed beside the others because the three arrive
// together, from the same missing column.) The em-dash is the honest placeholder
// for "there is nothing to compare here", and it is unanimous for every
// non-finite value, so one branch per case is one branch too many below.
export const formatSimilarity = (distance) => {
  if (distance === null || distance === undefined) {
    return "—";
  }

  const text = String(distance).trim();
  if (text === "") {
    return "—";
  }

  const value = Number(text);
  return Number.isFinite(value) ? value.toFixed(3) : "—";
};
