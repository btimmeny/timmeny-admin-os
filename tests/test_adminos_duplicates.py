from adminos.adapters.monday import MondayItem
from adminos.domain.duplicates import (
    STRONG_MATCH_SCORE,
    SUBSET_MATCH_SCORE,
    build_token_weights,
    find_duplicates,
    normalize_title,
    score_similarity,
    tokenize,
)


def item(
    name: str,
    status: str | None = "Not Yet Started",
    admin_os_id: str | None = None,
) -> MondayItem:
    return MondayItem(
        item_id=str(abs(hash(name)) % 10**8),
        name=name,
        group="Tasks | Action Items",
        status=status,
        admin_os_id=admin_os_id,
        action_date=None,
    )


BOARD = [
    item("Annual Taxes | KPMG"),
    item("Taxes | Annual FBAR Filing"),
    item("2026 Taxes (?) | Confirm KPMG (MX + USA)"),
    item("GS, NYC Visit | Submit Expenses"),
    item("GS | KO Shares (obtain cost basis)"),
    item("GS | Trainings (eTasks)"),
    item("GS | MyCompliance (myObligations)"),
    item("USA Taxes | Complete FBAR", status="Done"),
    item("Zac | Pimple Patches (CVS)", status="Done"),
    item("Viola | Inform of move-out date"),
]


def test_a_reply_prefix_is_not_part_of_the_title() -> None:
    assert normalize_title("RE: US Tax Briefing") == "us tax briefing"
    assert normalize_title("Fwd: Re: Annual filing") == "annual filing"


def test_punctuation_and_case_do_not_distinguish_titles() -> None:
    assert normalize_title("GS, NYC Visit | Submit Expenses") == "gs nyc visit submit expenses"


def test_filler_words_are_dropped_before_comparison() -> None:
    assert tokenize("Please update the info for my taxes") == {"taxes"}


def test_an_identical_title_scores_one() -> None:
    assert score_similarity("Annual Taxes | KPMG", "Annual Taxes | KPMG") == 1.0


def test_a_reordered_title_still_matches() -> None:
    """The board writes "Context | Action"; the same work can arrive reversed."""
    assert score_similarity("KPMG | Annual Taxes", "Annual Taxes | KPMG") == 1.0


def test_titles_with_no_word_in_common_do_not_match() -> None:
    """Character similarity alone is coincidence, not duplication."""
    assert score_similarity("Call plumber about kitchen leak", "Zac | Pimple Patches") == 0.0


def test_an_empty_title_matches_nothing() -> None:
    assert score_similarity("", "Annual Taxes | KPMG") == 0.0


def test_a_shared_common_word_counts_for_less_than_a_shared_rare_one() -> None:
    weights = build_token_weights([match.name for match in BOARD])

    common = score_similarity("GS | Book flights", "GS | Trainings (eTasks)", weights)
    rare = score_similarity("KPMG | Book flights", "Annual Taxes | KPMG", weights)

    assert rare > common


def test_a_word_absent_from_the_board_is_treated_as_distinctive() -> None:
    """Otherwise one shared common word makes any short title a duplicate."""
    weights = build_token_weights([match.name for match in BOARD])

    assert score_similarity("Buy milk", "GS | Buy a new laptop", weights) < STRONG_MATCH_SCORE


def test_an_existing_task_is_reported_as_a_strong_match() -> None:
    report = find_duplicates("Annual Taxes | KPMG", BOARD)

    assert report.has_strong_match is True
    assert report.matches[0].name == "Annual Taxes | KPMG"
    assert report.matches[0].is_strong is True


def test_a_reworded_existing_task_is_reported() -> None:
    report = find_duplicates("Submit the NYC visit expenses", BOARD)

    assert report.matches[0].name == "GS, NYC Visit | Submit Expenses"


def test_unrelated_work_reports_no_match() -> None:
    report = find_duplicates("Renew the car registration", BOARD)

    assert report.matches == []
    assert report.has_strong_match is False


def test_completed_work_is_reported_too() -> None:
    """Half this board is recurring: "you finished it last year" is the answer."""
    report = find_duplicates("USA Taxes | Complete FBAR", BOARD)

    assert report.matches[0].is_done is True


def test_matches_are_ranked_by_score() -> None:
    report = find_duplicates("Taxes | Annual FBAR Filing", BOARD)
    scores = [match.score for match in report.matches]

    assert scores == sorted(scores, reverse=True)


def test_an_open_match_outranks_a_done_match_of_equal_score() -> None:
    board = [item("Annual Taxes | KPMG", status="Done"), item("Annual Taxes | KPMG")]

    report = find_duplicates("Annual Taxes | KPMG", board)

    assert report.matches[0].is_done is False


def test_the_match_limit_is_respected() -> None:
    report = find_duplicates("Taxes | KPMG", BOARD, limit=2)

    assert len(report.matches) == 2


def test_a_higher_threshold_reports_fewer_matches() -> None:
    strict = find_duplicates("Taxes | KPMG", BOARD, threshold=0.9)
    loose = find_duplicates("Taxes | KPMG", BOARD, threshold=0.4)

    assert len(strict.matches) < len(loose.matches)


def test_the_report_records_what_was_compared() -> None:
    report = find_duplicates("RE: Annual Taxes | KPMG", BOARD)

    assert report.title == "RE: Annual Taxes | KPMG"
    assert report.normalized_title == "annual taxes kpmg"
    assert report.compared == len(BOARD)


def test_an_existing_admin_os_id_is_carried_into_the_match() -> None:
    board = [item("Annual Taxes | KPMG", admin_os_id="ao-1")]

    report = find_duplicates("Annual Taxes | KPMG", board)

    assert report.matches[0].admin_os_id == "ao-1"


def test_an_empty_board_reports_nothing() -> None:
    report = find_duplicates("Annual Taxes | KPMG", [])

    assert report.matches == []
    assert report.compared == 0


def test_a_contained_title_scores_below_an_identical_one() -> None:
    """A subset is a strong candidate, not the same task."""
    board = [item("KPMG | Next Steps")]

    subset = find_duplicates("Taxes | Confirm KPMG activities and next steps", board)
    identical = find_duplicates("KPMG | Next Steps", board)

    assert subset.matches[0].score == SUBSET_MATCH_SCORE
    assert identical.matches[0].score == 1.0


def test_a_contained_title_is_still_a_strong_match() -> None:
    board = [item("KPMG | Next Steps")]

    report = find_duplicates("Taxes | Confirm KPMG activities and next steps", board)

    assert report.has_strong_match is True


def test_reordered_words_still_score_as_identical() -> None:
    """Same words, different order: the board writes both ways round."""
    report = find_duplicates("KPMG | Annual Taxes", [item("Annual Taxes | KPMG")])

    assert report.matches[0].score == 1.0
