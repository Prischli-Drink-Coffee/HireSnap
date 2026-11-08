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
export const formatSimilarity = (distance) => {
  const value = Number(distance);
  return Number.isFinite(value) ? value.toFixed(3) : "—";
};
