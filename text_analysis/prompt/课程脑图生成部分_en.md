# ✅ English Prompt (ready to use)

**# Task Description**  
You will receive a course transcript as sentence-level entries in the format `startSecond-endSecond: content`, plus the overall course start and end times in seconds: `TOTAL_START`, `TOTAL_END`. Follow the steps below and output **valid JSON only** (RFC 8259 compliant).

## Required Steps
1. **Compute the total time range**  
   - The first user input line tells you the course start and end times. Record them as `"TOTAL_START"` and `"TOTAL_END"`.

2. **Key Points**  
   - Distill the core takeaway of the whole course in **10–20 English words**, output as a string.

3. **Fast skim of the whole segment: `document_skims`**  
   - `time`: must equal `"TOTAL_START-TOTAL_END"`.  
   - `overview`: **15–20 English words**, a high-level overview of the course.  
   - `content`: **150 English words**, covering the main storyline and takeaways.  
     - `content` **must** start with **`This section`** (e.g., `This section ...`).

4. **Three-level node tree `nodes`** (object; exactly 1 parent; `id` comes from `node_id`)  
   - `nodes` is an **object** (not an array); its `id` **must equal** the user-provided `node_id` (**output as a string**).  
   - `nodes.children` is an array of **3** child nodes, with continuous IDs starting from `"{node_id}.1"` (e.g., `2.1`, `2.2`, `2.3`).  
   - Each child has a `children` array of **3** grandchildren: `"{node_id}.x.1"`, `"{node_id}.x.2"`, `"{node_id}.x.3"` (continuous, no gaps).  
   - Every node (parent/child/grandchild) must contain: `id`, `label`, `time`.  
   - Grandchildren are leaves: **no** `children` field in any grandchild object.  
   - **Time constraints**:  
     - Parent interval: `"TOTAL_START-TOTAL_END"`.  
     - Child intervals are **fully** contained in the parent; each grandchild interval is **fully** contained in its **own** child.  
     - Siblings are sorted by start time ascending, intervals do not overlap, and start times are strictly increasing.

## Numbering & Format Hard Constraints
- **`node_id` rule**:  
  - Allowed values: positive integers **1, 2, 3, 4**. The user will only provide one of (1, 2, 3, 4). Output as a **string** (e.g., `"2"`).  
- **`id` regex validation**:  
  - Parent: `^\d+$` (must equal the provided `node_id`).  
  - Child: `^{node_id}\.\d+$` (e.g., `3.1`, `3.2`, … and **continuous**).  
  - Grandchild: `^{node_id}\.\d+\.\d+$` (e.g., `3.1.1`, `3.1.2`, … and **continuous**).  
- **No cross-parent prefixes**: if the parent is `3`, no child/grandchild IDs may start with `1.` or `2.` etc.

## Time Fields & Strict Validation
- Unified format: `"startSecond-endSecond"`, **integers only**, no decimals or multi-part ranges.  
- **Hard Rule A**: `start < end`; otherwise **rewrite the entire output**.  
- **Hard Rule B (hierarchical inclusion)**:  
  - Parent/Child: `p.start ≤ c.start < c.end ≤ p.end`  
  - Child/Grandchild: `c.start ≤ g.start < g.end ≤ c.end`  
- **Hard Rule C (siblings)**: strictly ascending by start time; intervals do not overlap.  
- **Hard Rule D (global)**: `TOTAL_START ≤ any node.start` and `any node.end ≤ TOTAL_END`.  
- **Any violation ⇒ immediately recompute and rewrite everything** (`nodes`, `document_skims`, `key_points`).

## JSON Syntax & Character Safety
- Output **valid JSON** (RFC 8259), directly parseable.  
- No spaces/tabs around key names; use half-width commas; no trailing commas.  
- **No comments** (`//`, `/* */`, `#`).  
- String values must **not** contain unescaped `\` or `"`.  
- **Do not** output angle brackets `< >` or any placeholder within them in the **final output**.  
- For `label`, prefer English quotation marks `"` or parentheses.

## Field Name Whitelist (no other top-level fields allowed)
- **Top-level**: `key_points`, `document_skims`, `nodes`  
- **In `document_skims`**: `time`, `overview`, `content`  
- **Node tree**:  
  - Parent/child nodes: `id`, `label`, `time`, `children`  
  - Grandchildren: `id`, `label`, `time` (**no `children`**)

## Self-checklist
- JSON lints; the set of keys matches the whitelist **exactly**.  
- `key_points` is a **string** with **4–10 English words**.  
- A: `document_skims.time == "TOTAL_START-TOTAL_END"`; `overview` **15–20 words**; `content` **150 words** and **starts with** `This section`.  
- B: `nodes.id == String(node_id)`; `nodes.time == "TOTAL_START-TOTAL_END"`.  
- C: `nodes.children.length == 3`; each child `children.length == 3`; child/grandchild ID prefixes all `{node_id}`, continuous with no gaps.  
- D: No ID matches `^{node_id}(?:\.\d+){3,}$`; no grandchild object contains `children`.  
- All `time` values match `^\d+-\d+$` and satisfy A/B/C/D; parent duration equals `"TOTAL_END" - "TOTAL_START"`.  
- Siblings sorted by start time; no overlaps; all times within the global range.  
- No unescaped quotes/backslashes; no comments; **no angle brackets or placeholders** in the **final output**.

## Output JSON Structure Example (format reference only; replace all placeholders with actual values)
{
  "key_points": "Concise core takeaway in 4–10 words",
  "document_skims": {
    "time": "TOTAL_START-TOTAL_END",
    "overview": "High-level overview in 15–20 words",
    "content": "This section provides a 150-word summary of the main storyline and takeaways..."
  },
  "nodes": {
    "id": "NODE_ID",
    "label": "Parent topic title",
    "time": "TOTAL_START-TOTAL_END",
    "children": [
      {
        "id": "NODE_ID.1",
        "label": "Child topic NODE_ID.1",
        "time": "NODE1_START-NODE1_END",
        "children": [
          { "id": "NODE_ID.1.1", "label": "Grandchild NODE_ID.1.1", "time": "NODE1_1_START-NODE1_1_END" },
          { "id": "NODE_ID.1.2", "label": "Grandchild NODE_ID.1.2", "time": "NODE1_2_START-NODE1_2_END" },
          { "id": "NODE_ID.1.3", "label": "Grandchild NODE_ID.1.3", "time": "NODE1_3_START-NODE1_3_END" }
        ]
      },
      {
        "id": "NODE_ID.2",
        "label": "Child topic NODE_ID.2",
        "time": "NODE2_START-NODE2_END",
        "children": [
          { "id": "NODE_ID.2.1", "label": "Grandchild NODE_ID.2.1", "time": "NODE2_1_START-NODE2_1_END" },
          { "id": "NODE_ID.2.2", "label": "Grandchild NODE_ID.2.2", "time": "NODE2_2_START-NODE2_2_END" },
          { "id": "NODE_ID.2.3", "label": "Grandchild NODE_ID.2.3", "time": "NODE2_3_START-NODE2_3_END" }
        ]
      },
      {
        "id": "NODE_ID.3",
        "label": "Child topic NODE_ID.3",
        "time": "NODE3_START-NODE3_END",
        "children": [
          { "id": "NODE_ID.3.1", "label": "Grandchild NODE_ID.3.1", "time": "NODE3_1_START-NODE3_1_END" },
          { "id": "NODE_ID.3.2", "label": "Grandchild NODE_ID.3.2", "time": "NODE3_2_START-NODE3_2_END" },
          { "id": "NODE_ID.3.3", "label": "Grandchild NODE_ID.3.3", "time": "NODE3_3_START-NODE3_3_END" }
        ]
      }
    ]
  }
}
