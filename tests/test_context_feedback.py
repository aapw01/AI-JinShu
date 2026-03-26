from app.services.generation.agents import (
    CharacterStateUpdateSchema,
    CharacterStateUpdatesSchema,
)


def test_character_state_schema_has_realm_and_emotion():
    """Schema must accept realm and emotional_state from LLM output."""
    raw = {
        "name": "林舟",
        "status": "alive",
        "location": "青云城",
        "realm": "金丹期",
        "emotional_state": "愤怒",
        "injuries": ["左臂骨折"],
        "new_items": [],
        "lost_items": [],
        "can_use_both_hands": False,
        "limitations": [],
        "forbidden_actions": [],
        "relationship_changes": [],
        "key_action": "突破金丹",
    }
    update = CharacterStateUpdateSchema(**raw)
    assert update.realm == "金丹期"
    assert update.emotional_state == "愤怒"


def test_character_state_schema_defaults_to_empty_string():
    """Schema defaults must set realm and emotional_state to empty string when omitted."""
    update = CharacterStateUpdateSchema(name="林舟")
    assert update.realm == ""
    assert update.emotional_state == ""


def test_character_state_updates_schema_roundtrip():
    payload = {"updates": [{"name": "林舟", "realm": "化神期", "emotional_state": "平静"}]}
    schema = CharacterStateUpdatesSchema(**payload)
    assert schema.updates[0].realm == "化神期"
    assert schema.updates[0].emotional_state == "平静"
