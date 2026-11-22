import random

import pytest

from modfather import themes
from modfather.engine import DAY, DUSK, ENDED, NIGHT, NO_LYNCH, Game, GameError


def make_theme(extra_slots=None, **settings):
    t = themes.new_theme("Test")
    t["settings"].update(settings)
    t["slots"] = [
        {"character": "Tony", "role_key": "godfather", "faction": "Mafia"},
        {"character": "Paulie", "role_key": "mafia_goon", "faction": "Mafia"},
        {"character": "Doc", "role_key": "doctor", "faction": "Town"},
        {"character": "Sherlock", "role_key": "cop", "faction": "Town"},
        {"character": "Frank", "role_key": "vigilante", "faction": "Town"},
        {"character": "Bob", "role_key": "vanilla_townie", "faction": "Town"},
        {"character": "Gus", "role_key": "bodyguard", "faction": "Town"},
    ] + (extra_slots or [])
    return themes.validate(t)


def started(theme=None, n=7):
    g = Game("g1", 1, 2, 99, theme or make_theme())
    for uid in range(1, n + 1):
        g.join(uid, f"P{uid}")
    g.start(random.Random(0))
    # re-seat deterministically: user i gets slot i-1 so tests can reason about roles
    for p in g.players.values():
        p.slot = p.user_id - 1
    return g


def by_char(g, ch):
    return next(p for p in g.players.values() if g.character(p) == ch)


# ---------------------------------------------------------------- votes / lynch
def test_majority_lynch_locks():
    g = started()
    assert g.phase == DAY and g.majority() == 4
    assert g.vote(1, 6) is None
    assert g.vote(2, 6) is None
    assert g.vote(3, 6) is None
    assert g.vote(4, 6) == 6          # 4th vote of 7 alive reaches majority
    out = g.lynch(6)
    assert out["lynched"].user_id == 6 and not g.player(6).alive
    assert g.locked and g.phase == DUSK
    with pytest.raises(GameError):
        g.vote(5, 1)


def test_vote_change_and_unvote():
    g = started()
    g.vote(1, 6)
    g.vote(1, 5)
    assert g.tally() == {5: [1]}
    g.unvote(1)
    assert g.tally() == {}
    with pytest.raises(GameError):
        g.unvote(1)


def test_dead_cannot_vote_or_be_voted():
    g = started()
    g.kill(6, "Modkilled")
    with pytest.raises(GameError):
        g.vote(6, 1)
    with pytest.raises(GameError):
        g.vote(1, 6)


def test_no_lynch_and_plurality():
    g = started()
    g.vote(1, NO_LYNCH)
    g.vote(2, 5)
    g.vote(3, 5)
    assert g.plurality() == 5
    g.vote(1, 3); g.vote(4, 3)
    assert g.plurality() is None      # tie -> no lynch by default


def test_doublevoter_and_hated_loved():
    t = themes.new_theme("w")
    t["slots"] = [{"character": c, "role_key": r, "faction": "Town"} for c, r in
                  [("A", "doublevoter"), ("B", "vanilla_townie"), ("C", "hated"),
                   ("D", "loved"), ("E", "vanilla_townie")]]
    t["slots"].append({"character": "M", "role_key": "mafia_goon", "faction": "Mafia"})
    g = started(themes.validate(t), n=6)
    assert g.majority() == 4
    assert g.threshold(3) == 3 and g.threshold(4) == 5
    assert g.vote(1, 3) is None       # weight 2
    assert g.vote(2, 3) == 3          # 3 >= hated threshold 3


def test_lynchproof_survives():
    t = make_theme([{"character": "Tank", "role_key": "lynchproof", "faction": "Town"}])
    g = started(t, n=8)
    for v in (1, 2, 3, 4, 5):
        r = g.vote(v, 8)
    assert r == 8
    out = g.lynch(8)
    assert out["survived"] and g.player(8).alive


# ---------------------------------------------------------------- player counts
def test_flexible_player_counts():
    t = make_theme([{"character": "Extra1", "role_key": "vanilla_townie", "faction": "Town", "min_players": 8},
                    {"character": "Extra2", "role_key": "roleblocker", "faction": "Mafia", "min_players": 9}])
    assert themes.supported_counts(t) == [7, 8, 9]
    g = Game("x", 1, 2, 99, t)
    g.set_max_players(8)
    for uid in range(1, 9):
        g.join(uid, f"P{uid}")
    with pytest.raises(GameError):
        g.join(9, "late")
    g.start(random.Random(1))
    chars = {g.character(p) for p in g.players.values()}
    assert "Extra1" in chars and "Extra2" not in chars
    g2 = Game("y", 1, 2, 99, t)
    for uid in range(1, 6):
        g2.join(uid, f"P{uid}")
    with pytest.raises(GameError, match="supports"):
        g2.start()


# ---------------------------------------------------------------- replacements / quitting
def test_replacement_keeps_state():
    g = started()
    g.vote(1, 6)
    g.vote(6, 2)
    g.claim(6, character="Bob")
    g.request_replacement(6)
    g.sub_join(50, "Sub")
    p = g.replace(6, 50, "Sub")
    assert g.player(6) is None and g.player(50) is p
    assert g.character(p) == "Bob" and p.replaced == [6]
    assert g.votes["1"] == 50 and g.votes["50"] == 2
    assert "50" in g.claims and 50 not in g.subs and not p.needs_replacement


def test_cannot_replace_with_existing_player_or_dead():
    g = started()
    with pytest.raises(GameError):
        g.replace(6, 5, "x")
    g.kill(6, "Modkilled Day 1")
    with pytest.raises(GameError):
        g.replace(6, 77, "x")


def test_modkill_clears_votes_and_triggers_win():
    g = started()
    g.vote(3, 1)
    g.kill(1, "Modkilled Day 1")
    assert "3" not in g.votes
    g.kill(2, "Modkilled Day 1")
    assert g.check_win() == ["Town"]


def test_signup_leave_and_max():
    g = Game("x", 1, 2, 99, make_theme())
    g.join(1, "a")
    with pytest.raises(GameError):
        g.join(1, "a")
    g.leave(1)
    assert g.signups == []


# ---------------------------------------------------------------- night
def night(g):
    g.lynch(None)
    g.start_night()


def test_faction_kill_and_doctor_save():
    g = started()
    night(g)
    g.submit_faction_kill(2, 6)          # Paulie kills Bob
    g.submit_action(3, [6])              # Doc protects Bob
    r = g.resolve_night()
    assert r["deaths"] == []
    g.apply_night(r)
    assert g.player(6).alive


def test_bodyguard_dies_instead():
    g = started()
    night(g)
    g.submit_faction_kill(2, 6)
    g.submit_action(7, [6])
    r = g.resolve_night()
    assert [d[0] for d in r["deaths"]] == [7]


def test_godfather_reads_innocent_cop_results():
    g = started()
    night(g)
    g.submit_action(4, [1])
    r = g.resolve_night()
    assert "innocent" in r["results"][4][0]
    g2 = started()
    night(g2)
    g2.submit_action(4, [2])
    assert "guilty" in g2.resolve_night()["results"][4][0]


def test_roleblock_stops_kill():
    t = make_theme([{"character": "Rita", "role_key": "roleblocker", "faction": "Town"}])
    g = started(t, n=8)
    night(g)
    g.submit_faction_kill(2, 6)
    g.submit_action(8, [2])
    r = g.resolve_night()
    assert r["deaths"] == [] and 2 in r["blocked"]


def test_bus_driver_swaps_kill():
    t = make_theme([{"character": "Otto", "role_key": "bus_driver", "faction": "Town"}])
    g = started(t, n=8)
    night(g)
    g.submit_faction_kill(2, 6)
    g.submit_action(8, [6, 5])
    r = g.resolve_night()
    assert [d[0] for d in r["deaths"]] == [5]


def test_tracker_and_watcher():
    t = make_theme([{"character": "Tess", "role_key": "tracker", "faction": "Town"},
                    {"character": "Walt", "role_key": "watcher", "faction": "Town"}])
    g = started(t, n=9)
    night(g)
    g.submit_action(3, [6])        # doc visits Bob
    g.submit_action(8, [3])        # track doc
    g.submit_action(9, [6])        # watch Bob
    r = g.resolve_night()
    assert "P6 (Bob)" in r["results"][8][0]
    assert "P3 (Doc)" in r["results"][9][0]


def test_vigilante_one_shot_and_manual_actions():
    t = make_theme([{"character": "Zed", "role_key": "arsonist", "faction": "Third Party"}])
    t["slots"][4]["modifiers"] = ["one_shot"]
    t = themes.validate(t)
    g = started(t, n=8)
    night(g)
    g.submit_action(5, [1])
    g.submit_action(8, [3], note="douse")
    r = g.resolve_night()
    assert [d[0] for d in r["deaths"]] == [1]
    assert r["manual"] and "douse" in r["manual"][0]["text"]
    g.apply_night(r)
    g.start_day()
    g.lynch(None); g.start_night()
    with pytest.raises(GameError, match="no uses"):
        g.submit_action(5, [2])


def test_mafia_parity_win():
    g = started()
    for uid in (3, 4, 5):
        g.kill(uid, "x")
    assert g.check_win() == ["Mafia"]      # 2 mafia vs 2 town


def test_round_trip_serialization():
    g = started()
    g.vote(1, 6)
    g.claim(2, role="Doctor")
    g2 = Game.from_dict(g.to_dict())
    assert g2.to_dict() == g.to_dict()
    assert g2.player(1).name == "P1"


def test_find_by_character_and_name():
    g = started()
    assert g.find("sherl").user_id == 4
    assert g.find("P5").user_id == 5
    assert g.find("<@6>").user_id == 6


# ---------------------------------------------------------------- claims
from modfather import render  # noqa: E402


def test_claim_character_only_implies_role():
    g = started()
    c = g.claim(3, character="doc")          # prefix match to "Doc"
    assert c["character"] == "Doc" and c["implied_role"] == "Doctor" and c["role"] == ""
    assert "Doc → Doctor" in render.claims_board(g)


def test_claim_role_only_implies_character():
    g = started()
    c = g.claim(4, role="cop")
    assert c["role"] == "Cop" and c["implied_characters"] == ["Sherlock"]


def test_claim_both_consistency():
    g = started()
    assert g.claim(4, character="Sherlock", role="Cop")["consistent"] is True
    c = g.claim(1, character="Bob", role="Doctor")
    assert c["consistent"] is False
    assert "not a pair" in render.claim_text(g, c)


def test_counterclaims_character_and_role():
    g = started()
    g.claim(3, role="Doctor")                 # implies Doc
    g.claim(1, character="Doc")               # godfather fakeclaims Doc
    cc = g.counterclaims()
    assert sorted(cc["characters"]["Doc"]) == [1, 3]
    assert sorted(cc["roles"]["Doctor"][0]) == [1, 3] and cc["roles"]["Doctor"][1] == 1
    assert any("Doc" in ln for ln in render.counterclaim_lines(g, 1))
    assert render.counterclaim_lines(g, 5) == []


def test_claiming_dead_characters_flagged():
    g = started()
    g.kill(6, "Killed Night 1")               # Bob flips Vanilla Townie
    g.claim(1, character="Bob")
    assert 1 in g.counterclaims()["characters"]["Bob"]


def test_claim_unknown_names_kept_and_history():
    g = started()
    c = g.claim(2, character="Batman")
    assert c["character"] == "Batman" and c["character_known"] is False
    g.claim(2, role="Vigilante")
    g.unclaim(2)
    board = render.claims_board(g, history=True)
    assert "Batman" in board and "withdrew" in board
    assert "2" not in g.claims


def test_claims_survive_replacement():
    g = started()
    g.claim(5, character="Frank")
    g.replace(5, 60, "New")
    assert g.claims["60"]["character"] == "Frank"


# ---------------------------------------------------------------- role PMs
def test_every_role_has_starter_text_with_confidence():
    from modfather import catalog
    roles = catalog.mu_roles()
    assert len(roles) == 475
    assert all(r["description"] for r in roles)
    assert {r["confidence"] for r in roles} <= {"standard", "typical", "check"}


def test_starter_pm_and_rendering():
    t = make_theme([{"character": "Zed", "role_key": "ability_thief", "faction": "Third Party"}])
    doc = themes.starter_pm(t, t["slots"][2])
    assert "{character}" in doc and "Each night, protect" in doc
    assert "You win when every threat to the Town is dead." in doc
    assert "best guess" not in doc                      # Doctor is 'standard'
    thief = themes.starter_pm(t, t["slots"][7])
    assert "best guess" in thief and "[GM: write this role's win condition.]" in thief
    mafia = themes.starter_pm(t, t["slots"][0])
    assert "the {faction} make up at least half" in mafia
    t["slots"][2]["pm_text"] = doc
    t["slots"][2]["modifiers"] = ["bulletproof"]
    out = themes.render_pm(t, t["slots"][2], "<@3>")
    assert out.startswith("You are **Doc**.") and "[GM:" not in out and "\n\n\n" not in out
    assert "**Bulletproof Doctor**, aligned with the **Town**" in out
    assert themes.unfinished_pms(t) == ["Doc"]


def test_role_card_uses_custom_pm():
    t = make_theme()
    t["slots"][3]["pm_text"] = "You are **{character}**, the sharpest mind in London.\n[GM: tweak]\nYou are the **{role}**."
    g = started(themes.validate(t))
    card = render.role_card(g, g.player(4))
    assert card["description"].startswith("You are **Sherlock**, the sharpest")
    assert "[GM" not in card["description"]
    names = [f[0] for f in card["fields"]]
    assert "At night" in names and "Ability" not in names and "Win condition" not in names
    plain = render.role_card(g, g.player(3))            # no custom PM -> starter card
    assert [f[0] for f in plain["fields"]][:3] == ["Role", "Faction", "Ability"]
