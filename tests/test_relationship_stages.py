from app.domain.relationship import (
    RelationshipStage,
    describe_relationship,
    is_romantic,
    stage_for_trust,
)


def test_default_trust_is_stranger():
    assert stage_for_trust(0.0) is RelationshipStage.STRANGER


def test_stage_ladder_boundaries():
    assert stage_for_trust(-1.0) is RelationshipStage.BLOCKED
    assert stage_for_trust(-0.6) is RelationshipStage.BLOCKED
    assert stage_for_trust(-0.59) is RelationshipStage.STRANGER
    assert stage_for_trust(-0.5) is RelationshipStage.STRANGER
    assert stage_for_trust(0.0) is RelationshipStage.STRANGER
    assert stage_for_trust(0.009) is RelationshipStage.STRANGER
    assert stage_for_trust(0.01) is RelationshipStage.ACQUAINTANCE
    assert stage_for_trust(0.05) is RelationshipStage.ACQUAINTANCE
    assert stage_for_trust(0.09) is RelationshipStage.ACQUAINTANCE
    assert stage_for_trust(0.10) is RelationshipStage.FRIEND
    assert stage_for_trust(0.24) is RelationshipStage.FRIEND
    assert stage_for_trust(0.25) is RelationshipStage.CLOSE_FRIEND
    assert stage_for_trust(0.39) is RelationshipStage.CLOSE_FRIEND
    assert stage_for_trust(0.40) is RelationshipStage.BEST_FRIEND
    assert stage_for_trust(0.54) is RelationshipStage.BEST_FRIEND
    assert stage_for_trust(0.55) is RelationshipStage.CRUSH
    assert stage_for_trust(0.64) is RelationshipStage.CRUSH
    assert stage_for_trust(0.65) is RelationshipStage.FLIRTING
    assert stage_for_trust(0.71) is RelationshipStage.FLIRTING
    assert stage_for_trust(0.72) is RelationshipStage.IN_LOVE
    assert stage_for_trust(0.77) is RelationshipStage.IN_LOVE


def test_romantic_band_is_gender_aware():
    assert stage_for_trust(0.78, "female") is RelationshipStage.GIRLFRIEND
    assert stage_for_trust(0.80, "male") is RelationshipStage.BOYFRIEND
    assert stage_for_trust(0.78, None) is RelationshipStage.PARTNER
    assert stage_for_trust(0.78, "") is RelationshipStage.PARTNER
    assert stage_for_trust(0.88, "female") is RelationshipStage.PARTNER
    assert stage_for_trust(0.94, "male") is RelationshipStage.PARTNER
    assert stage_for_trust(0.95, "female") is RelationshipStage.ENGAGED
    assert stage_for_trust(0.98, "male") is RelationshipStage.ENGAGED
    assert stage_for_trust(0.99, "male") is RelationshipStage.FAMILY
    assert stage_for_trust(1.0) is RelationshipStage.FAMILY


def test_trust_clamping():
    assert stage_for_trust(5.0) is RelationshipStage.FAMILY
    assert stage_for_trust(-5.0) is RelationshipStage.BLOCKED


def test_romantic_flags():
    assert is_romantic(RelationshipStage.CRUSH)
    assert is_romantic(RelationshipStage.IN_LOVE)
    assert not is_romantic(RelationshipStage.FRIEND)
    assert not is_romantic(RelationshipStage.STRANGER)


def test_describe_relationship_known_contact():
    text = describe_relationship(0.30, "Alice")
    assert "Alice" in text
    assert "close friend" in text
    assert "+0.30" in text


def test_describe_relationship_unknown_contact_is_stranger():
    text = describe_relationship(0.0, "Stranger", is_unknown=True)
    assert "unknown contact" in text
    assert "stranger" in text


def test_describe_relationship_blocked_includes_instruction():
    text = describe_relationship(-0.8, "Spam Bot")
    assert "blocked" in text


def test_describe_relationship_gender_aware_romantic():
    text = describe_relationship(0.80, "Sam", persona_gender="female")
    assert "girlfriend" in text
