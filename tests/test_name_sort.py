"""The sort key is the contract every client agrees on, so pin its rules."""

from datetime import UTC, datetime

from app.models.card import CapturedCardDocument, CoreFields
from app.models.user_card import UserCardDocument
from app.services.name_sort import ROMANIZED_LAST_NAME_KEY, derive_sort_key


def core(**fields) -> dict:
    return CoreFields(**fields).model_dump()


class TestDeriveSortKey:
    def test_case_1_both_names_sort_on_the_english_family_name(self):
        fields = core(
            name="Li Shan Shan",
            first_name="Shan Shan",
            last_name="Li",
            name_cn="李珊珊",
        )
        assert derive_sort_key(fields) == ("li", "last_en")

    def test_case_2_only_a_given_name_carries_the_sort(self):
        assert derive_sort_key(core(name="Chris", first_name="Chris")) == ("chris", "first_en")

    def test_case_3_given_name_beside_a_chinese_full_name(self):
        fields = core(name="Lily", first_name="Lily", name_cn="李珊珊")
        assert derive_sort_key(fields) == ("lily", "first_en")

    def test_a_romanised_family_name_beats_a_guess(self):
        fields = core(name="陳大文", name_cn="陳大文")
        custom = {ROMANIZED_LAST_NAME_KEY: "Chan"}
        assert derive_sort_key(fields, custom) == ("chan", "romanized_cn")

    def test_a_legacy_card_is_guessed_and_says_so(self):
        """Cards written before the split still sort, and the basis admits why."""
        assert derive_sort_key(core(name="Chris Huang")) == ("huang", "guessed_en")

    def test_a_company_only_card_files_under_the_company(self):
        """Guessing a family name here would file the card under C for "CO."."""
        fields = core(name="ILIA JEWELLERY CO.", company_name="ILIA JEWELLERY CO.")
        assert derive_sort_key(fields) == ("ilia jewellery co.", "company")

    def test_a_chinese_only_card_has_no_latin_key(self):
        """An empty key is what puts the card in the 中文 section at the end."""
        assert derive_sort_key(core(name="陳大文")) == ("", "none")

    def test_blank_parts_do_not_count_as_a_split(self):
        fields = core(name="Chris Huang", first_name="  ", last_name="")
        assert derive_sort_key(fields) == ("huang", "guessed_en")


class TestDocumentsDeriveTheirOwnKey:
    def test_captured_card(self):
        document = CapturedCardDocument(
            owner_user_id="user-1",
            scanned_at=datetime.now(UTC),
            core_fields=CoreFields(name="Wong Ka Ming", first_name="Ka Ming", last_name="Wong"),
        )
        assert (document.sort_key, document.sort_basis) == ("wong", "last_en")

    def test_user_card(self):
        now = datetime.now(UTC)
        document = UserCardDocument(
            owner_user_id="user-1",
            core_fields=CoreFields(name="Ma Chun Wai", first_name="Chun Wai", last_name="Ma"),
            design_id="classic",
            design_type="preset",
            created_at=now,
            updated_at=now,
        )
        assert (document.sort_key, document.sort_basis) == ("ma", "last_en")

    def test_a_stale_key_is_recomputed_rather_than_trusted(self):
        document = CapturedCardDocument(
            owner_user_id="user-1",
            scanned_at=datetime.now(UTC),
            core_fields=CoreFields(name="Chris Huang", last_name="Huang"),
            sort_key="stale",
            sort_basis="company",
        )
        assert (document.sort_key, document.sort_basis) == ("huang", "last_en")


class TestCoreFieldsAcceptsTheSplit:
    def test_the_app_can_save_the_split(self):
        """The edit form PUTs the whole core_fields object; extra=forbid would 422."""
        fields = CoreFields(
            name="Li Shan Shan",
            first_name="Shan Shan",
            last_name="Li",
            name_cn="李珊珊",
        )
        assert fields.last_name == "Li"
        assert fields.name_cn == "李珊珊"

    def test_blank_optional_names_normalise_to_none(self):
        fields = CoreFields(name="Chris Huang", first_name="   ", name_cn="")
        assert fields.first_name is None
        assert fields.name_cn is None
