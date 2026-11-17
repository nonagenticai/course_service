You are a course curriculum designer. Create a structured course curriculum from the following website analysis.

## Source Material

**Site:** {{ site_name }}
**Description:** {{ site_description }}

### Topics Extracted ({{ topics | length }} total)

{% for topic in topics[:150] %}
- **{{ topic.title }}** ({{ topic.complexity }})
  URL: {{ topic.page_url }}
  {% if topic.content_summary %}Summary: {{ topic.content_summary[:800] }}{% endif %}
  {% if topic.subtopics %}Subtopics: {{ topic.subtopics | join(', ') }}{% endif %}
{% endfor %}

{% if key_themes %}
### Key Themes
{% for theme in key_themes %}
- {{ theme }}
{% endfor %}
{% endif %}

{% if user_context %}
### User Preferences
{% for key, value in user_context.items() %}
- {{ key }}: {{ value }}
{% endfor %}
{% endif %}

## Requirements

1. Create a course with logically grouped **modules** (3-15 modules)
2. Each module contains **lessons** (2-20 lessons per module)
3. Total lessons must not exceed **{{ max_lessons }}**
4. Each lesson targets **{{ target_duration_minutes }} minutes** ({{ target_duration_seconds }} seconds)
5. Lessons should follow a logical learning progression within each module
6. Module ordering should go from foundational to advanced concepts
7. Each lesson should focus on a single, well-defined topic
8. Avoid duplicate lesson titles

## Output Format

Respond with ONLY a JSON object (no markdown, no explanation):

```json
{
  "title": "Course Title",
  "description": "One-paragraph course description",
  "modules": [
    {
      "title": "Module 1 Title",
      "description": "Module description",
      "lessons": [
        {
          "title": "Lesson Title",
          "topic": "Specific topic covered",
          "description": "What the learner will understand after this lesson",
          "video_type": "mixed",
          "target_duration_seconds": {{ target_duration_seconds }}
        }
      ]
    }
  ]
}
```

CRITICAL: Output ONLY valid JSON. No markdown code fences, no explanatory text.
