# Job-fit scoring prompt

Evaluate the candidate profile against the job below. Return **only** a JSON object matching the scoring schema: `score` (integer 0–100), `recommendation`, `strengths`, `gaps`, and `reasoning`.

## Candidate profile

{{candidate_profile}}

## Job

- Title: {{job_title}}
- Company: {{job_company}}
- Location: {{job_location}}
- Description:

{{job_description}}
