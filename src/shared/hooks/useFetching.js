import { useState } from "react";

// Small async wrapper used by every table: exposes the in-flight flag and the
// error message of the last call, and never lets a rejection escape into an
// unhandled promise.
export const useFetching = (callback) => {
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState("");

  const fetchingCallback = async () => {
    try {
      setFetching(true);
      await callback();
      setError("");
    } catch (e) {
      setError((e && e.message) || "Request failed");
    } finally {
      setFetching(false);
    }
  };

  return [fetchingCallback, error, fetching];
};
