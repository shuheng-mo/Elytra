"""Agent self-evolution: experience pool + user feedback.

Two persistence layers and two LangGraph nodes collaborate to make the
agent "learn" from its own mistakes and from user signals, without ever
training or fine-tuning a model:

* ``ExperienceStore`` — writes ``(failed_sql, error, corrected_sql)`` pairs
  after successful self-correction cycles, and retrieves similar past cases
  via pgvector cosine similarity on the ``user_query`` embedding.

* ``FeedbackStore`` — writes user up/down votes collected from the UI,
  retrieves golden (positive) and negative (negative) examples separately.

* ``retrieve_experience_node`` — runs between ``filter_by_permission`` and
  ``generate_sql``. Merges the three signals into ``state["dynamic_examples"]``
  for the prompt builder to pick up.

* ``save_experience_node`` — runs after ``format_result`` on the success
  path, but only when ``retry_count > 0`` (i.e. the agent actually learned
  something this run).

The design is prompt-layer only: no new model training, no gradients. The
tradeoff is that experience is explicit and auditable but bounded by
prompt size.
"""

from src.evolution.experience_store import ExperienceRecord, ExperienceStore
from src.evolution.feedback_store import FeedbackStore

__all__ = ["ExperienceRecord", "ExperienceStore", "FeedbackStore"]
