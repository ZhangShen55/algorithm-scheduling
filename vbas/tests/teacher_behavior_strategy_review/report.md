# teacher_behavior strategy review

## Current postprocess baseline

- images: 692
- images with raw detections: 690
- images with no raw detections: 2 (`00000125-ouay.jpg`, `00000213-pmta.jpg`)
- current strategy: `multi_label_topmost`
- known wrong-main-subject images: 9 (`19`, `55`, `62`, `63`, `64`, `66`, `181`, `647`, `688`)
- corrected note: `205` is a valid teacher detection and is no longer treated as an abnormal sample.

## Proposed offline strategy

- Build subject clusters with `SubjectClusterIoU = 0.45`.
- For selecting the main subject, use posture confidence first: max confidence among `sit` / `stand`.
- Use behavior labels `bbwriting` / `teach` as attached labels, not as the primary subject identity when posture candidates exist.
- Fall back to max confidence only when no posture candidate exists.

## Offline comparison result

- changed images vs current strategy: 25
- changed known wrong-main-subject images: 9 / 9
- changed other images: 16
- normal proposed label pattern: `stand+teach` 520, `stand` 92, `bbwriting+stand` 54, `bbwriting+stand+teach` 14, `sit+stand` 1

The 16 other changes are not automatically regressions. Several are hidden current-strategy mistakes or same-label improvements caused by merging same-teacher boxes with lower IoU.

See `strategy_compare.csv` for per-image details.
