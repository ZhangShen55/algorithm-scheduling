# teacher_behavior.pt eval

- images: 692
- model names: {0: 'sit', 1: 'stand', 2: 'bbwriting', 3: 'teach'}
- image_size: 640
- predict_conf: 0.25
- merge_iou: 0.8
- subject_cluster_iou: 0.45
- keep_only_main_subject: True
- main_subject_strategy: posture_confidence
- object100 count distribution: {1: 690, 0: 2}
- images with object100_count > 1: 0
- images with object100_count != 1: 2

## Outputs

- summary.csv
- raw_detections.csv
- response_results.json
- multi_100_images.txt
- run_config.json
- annotated_problem/
