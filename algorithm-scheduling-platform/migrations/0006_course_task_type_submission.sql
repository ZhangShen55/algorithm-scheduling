BEGIN;

ALTER TABLE course_task_types
    ADD COLUMN submission_id uuid;

UPDATE course_task_types
SET submission_id = gen_random_uuid()
WHERE submission_id IS NULL;

ALTER TABLE course_task_types
    ALTER COLUMN submission_id SET NOT NULL;

COMMENT ON COLUMN course_task_types.submission_id IS
    '创建该任务类型的课程提交批次标识，同一次提交新增的任务类型共享该值，后续提交使用新值';

COMMIT;
