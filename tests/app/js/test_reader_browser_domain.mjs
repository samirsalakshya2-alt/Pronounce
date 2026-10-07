import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
globalThis.ReaderCore = require(resolve(here, "../../../src/pronunciation_lab/app/static/reader-core.js"));
const { segmentArticle, attemptStatus } = require(resolve(
  here, "../../../src/pronunciation_lab/app/static/reader-browser.js",
));

const articleId = "a".repeat(32);
const text = "Dr. Smith read carefully. This sentence follows.\n\nA final sentence.";
const segments = segmentArticle(text, articleId);
assert.deepEqual(segments.map((item) => item.text), [
  "Dr. Smith read carefully.", "This sentence follows.", "A final sentence.",
]);
assert.deepEqual(segments.map((item) => item.paragraph_index), [0, 0, 1]);
assert.equal(segments[0].char_start, 0);
assert.equal(text.slice(segments[0].char_start, segments[0].char_end).replace(/\s+/gu, " ").trim(),
  segments[0].text);
assert.throws(() => segmentArticle(" \n ", articleId), /no words/);
assert.throws(() => segmentArticle("a".repeat(50001), articleId), /50000/);

const base = {
  state: "ANALYZED",
  user_disposition: null,
};
const primary = {
  state: "SUCCEEDED", engine_id: "wav2vec2_raw",
  target_confirmation: { state: "AMBIGUOUS" },
  boundary: { state: "TARGET_ONLY", feedback_withheld: false },
};
assert.equal(attemptStatus(base, primary, "wav2vec2_raw").needs_decision, true);
assert.equal(attemptStatus({ ...base, user_disposition: "kept" }, primary, "wav2vec2_raw").summary, null);
assert.equal(attemptStatus({ ...base, user_disposition: "discarded" }, primary, "wav2vec2_raw").summary_code, "discarded");
assert.equal(attemptStatus(base, {
  ...primary, boundary: { state: "BOUNDARY_UNCERTAIN", feedback_withheld: true },
}, "wav2vec2_raw").feedback, "withheld_boundary");
assert.equal(attemptStatus({ ...base, state: "TOO_SHORT" }, null, "wav2vec2_raw").identity, "TOO_SHORT");

console.log("browser Reader domain tests passed");
