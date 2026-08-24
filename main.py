# main.py
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Any, Literal, NamedTuple

import orjson
import pandas as pd

DATA_DIR: Path = Path(__file__).resolve().parent
LOCAL_SCHEDULES_DIR: Path = DATA_DIR / "schedules" / "current"

version_match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", DATA_DIR.name)
SCRIPT_VERSION = "".join(version_match.groups()) if version_match else "413"

COMPRESSED_SCHEDULE_FILE: Path = DATA_DIR / f"database_v{SCRIPT_VERSION}.json"

PAIR_TIMES_TO_NUM: dict[str, int] = {
    "9:00:00": 1,
    "10:45:00": 2,
    "13:00:00": 3,
    "14:45:00": 4,
    "16:30:00": 5,
    "18:15:00": 6,
}
PAIR_NUM_TO_TIME: dict[int, str] = {v: k for k, v in PAIR_TIMES_TO_NUM.items()}
TIME_STR_TO_PAIR_IDX: dict[str, int] = {
    t: n - 1 for t, n in PAIR_TIMES_TO_NUM.items()
}


class Lesson(NamedTuple):
    subject_id: int
    lector_id: int
    type_id: int
    room_id: int


class HybridCompressor:
    """
    Выполняет сжатие с использованием Pandas для векторизации операций.
    """

    def __init__(self):
        self.dictionaries = {}
        self.meta_patterns = {}
        self.semester_start_date = datetime(2026, 8, 31)
        self.global_week_map = []

    def _build_storage_from_series(
        self, series: pd.Series
    ) -> tuple[list, dict]:
        """Создает _list и _map из pandas Series, отсортированной по частоте."""

        item_list = [None]
        item_map = {}

        item_list.extend(series.index)

        item_map = {val: i for i, val in enumerate(item_list)}
        return item_list, item_map

    def process_schedules_dataframe(self, all_lessons_df: pd.DataFrame):
        if all_lessons_df.empty:
            return {}

        df = all_lessons_df.copy()

        df["date_obj"] = pd.to_datetime(df["date"], format="%d.%m.%Y")
        isocal = df["date_obj"].dt.isocalendar()
        df["year"] = isocal.year
        df["week"] = isocal.week
        df["weekday"] = isocal.day - 1
        df = df[df["weekday"] < 6].copy()

        lector_parts = df["lector"].str.extract(
            r"^(.*?)\s*(\([^)]+\))?$", expand=True
        )
        main_name = lector_parts[0].str.strip().str.title()
        suffix = lector_parts[1].str.lower().fillna("")
        df["lector_normalized"] = (main_name + " " + suffix).str.strip()

        self.semester_start_date = df["date_obj"].min() - timedelta(
            days=df["date_obj"].min().weekday()
        )

        # ИЗМЕНЕНО: Добавлена обработка 'group' как словаря
        for col, name in [
            ("group", "groups"),
            ("subject", "subjects"),
            ("lector_normalized", "lectors"),
            ("type", "types"),
            ("room", "rooms"),
        ]:
            counts = df[col].value_counts()
            lst, m = self._build_storage_from_series(counts)
            self.dictionaries[name] = {"_list": lst, "_map": m}

        # ИЗМЕНЕНО: Добавлено создание group_id
        df["group_id"] = (
            df["group"]
            .map(self.dictionaries["groups"]["_map"])
            .fillna(0)
            .astype(int)
        )
        df["subject_id"] = (
            df["subject"]
            .map(self.dictionaries["subjects"]["_map"])
            .fillna(0)
            .astype(int)
        )
        df["lector_id"] = (
            df["lector_normalized"]
            .map(self.dictionaries["lectors"]["_map"])
            .fillna(0)
            .astype(int)
        )
        df["type_id"] = (
            df["type"]
            .map(self.dictionaries["types"]["_map"])
            .fillna(0)
            .astype(int)
        )
        df["room_id"] = (
            df["room"]
            .map(self.dictionaries["rooms"]["_map"])
            .fillna(0)
            .astype(int)
        )

        lesson_cols = ["subject_id", "lector_id", "type_id", "room_id"]
        lesson_counts = (
            df.groupby(lesson_cols).size().sort_values(ascending=False)
        )
        self.meta_patterns["lessons"] = [None] + [
            list(idx) for idx in lesson_counts.index
        ]

        df_lessons = pd.DataFrame(
            self.meta_patterns["lessons"][1:], columns=lesson_cols
        )
        df_lessons["lesson_id"] = range(1, len(df_lessons) + 1)

        df = pd.merge(df, df_lessons, on=lesson_cols, how="left")

        df["lesson_id"] = df["lesson_id"].fillna(0).astype(int)

        # ИЗМЕНЕНО: Индекс pivot'а теперь использует group_id вместо group
        day_pivot = df.pivot_table(
            index=["group_id", "year", "week", "weekday"],
            columns="pair_idx",
            values="lesson_id",
            fill_value=0,
        )
        day_pivot = day_pivot.astype(int)

        for i in range(6):
            if i not in day_pivot.columns:
                day_pivot[i] = 0
        day_pivot = day_pivot[range(6)]

        day_patterns_counts = (
            day_pivot.groupby(list(range(6)))
            .size()
            .sort_values(ascending=False)
        )
        self.meta_patterns["days"] = [[]] + [
            list(idx) for idx in day_patterns_counts.index
        ]
        day_map = {
            idx: i + 1 for i, idx in enumerate(day_patterns_counts.index)
        }
        day_pivot["day_pattern_tuple"] = [tuple(x) for x in day_pivot.values]
        day_pivot["day_id"] = (
            day_pivot["day_pattern_tuple"].map(day_map).fillna(0).astype(int)
        )

        week_pivot = day_pivot.reset_index().pivot_table(
            index=["group_id", "year", "week"],
            columns="weekday",
            values="day_id",
            fill_value=0,
        )
        week_pivot = week_pivot.astype(int)

        for i in range(6):
            if i not in week_pivot.columns:
                week_pivot[i] = 0
        week_pivot = week_pivot[range(6)]

        week_patterns_counts = (
            week_pivot.groupby(list(range(6)))
            .size()
            .sort_values(ascending=False)
        )
        self.meta_patterns["weeks"] = [[]] + [
            list(idx) for idx in week_patterns_counts.index
        ]
        week_map = {
            idx: i + 1 for i, idx in enumerate(week_patterns_counts.index)
        }

        week_pivot["week_pattern_tuple"] = [
            tuple(x) for x in week_pivot.values
        ]
        week_pivot["week_id"] = (
            week_pivot["week_pattern_tuple"]
            .map(week_map)
            .fillna(0)
            .astype(int)
        )

        # ИЗМЕНЕНО: schedules теперь - это массив, а не словарь.
        # Его размер равен количеству групп в словаре.
        num_groups = len(self.dictionaries["groups"]["_list"])
        final_schedules = [[] for _ in range(num_groups)]

        unique_weeks_df = df[["year", "week"]].drop_duplicates()
        self.global_week_map = sorted([
            tuple(x) for x in unique_weeks_df.values
        ])
        week_to_global_idx = {
            week: i for i, week in enumerate(self.global_week_map)
        }

        num_global_weeks = len(self.global_week_map)

        # ИЗМЕНЕНО: Группировка по group_id
        grouped_schedules = week_pivot.reset_index().groupby("group_id")

        for group_id, data in grouped_schedules:
            group_week_list = [0] * num_global_weeks
            for _, row in data.iterrows():
                week_tuple = (row["year"], row["week"])
                global_idx = week_to_global_idx.get(week_tuple)
                if global_idx is not None:
                    group_week_list[global_idx] = row["week_id"]
            # ИЗМЕНЕНО: Расписание записывается по индексу группы
            if group_id < len(final_schedules):
                final_schedules[group_id] = group_week_list

        return final_schedules

    def get_database(self, schedules):
        return {
            "version": SCRIPT_VERSION,  # Добавлено: версия скрипта
            "semester_info": {
                "start_date": self.semester_start_date.strftime("%d.%m.%Y"),
                "global_week_map": self.global_week_map,
            },
            "dictionaries": {
                name: data["_list"] for name, data in self.dictionaries.items()
            },
            "meta_patterns": self.meta_patterns,
            "schedules": schedules,
            "inverted_indices": self._build_inverted_indices(
                schedules
            ),  # Перемещено сюда
        }

    def _build_inverted_indices(self, schedules) -> dict:
        room_to_groups_map = defaultdict(set)
        lector_to_groups_map = defaultdict(set)

        lessons = self.meta_patterns["lessons"]
        days = self.meta_patterns["days"]
        weeks = self.meta_patterns["weeks"]

        for group_id, group_schedule_list in enumerate(schedules):
            if not group_schedule_list:
                continue

            used_week_ids = {
                week_id for week_id in group_schedule_list if week_id > 0
            }
            for week_id in used_week_ids:
                if not weeks[week_id]:
                    continue
                for day_id in weeks[week_id]:
                    if not days[day_id]:
                        continue
                    for lesson_id in days[day_id]:
                        if not lesson_id:
                            continue
                        lesson_data = Lesson(*lessons[lesson_id])
                        room_to_groups_map[str(lesson_data.room_id)].add(
                            group_id
                        )
                        lector_to_groups_map[str(lesson_data.lector_id)].add(
                            group_id
                        )

        return {
            "rooms": {k: sorted(v) for k, v in room_to_groups_map.items()},
            "lectors": {k: sorted(v) for k, v in lector_to_groups_map.items()},
        }


class HybridDecompressor:
    def __init__(self, database: dict[str, Any]):
        self.db = database
        self.start_date = datetime.strptime(
            database["semester_info"]["start_date"], "%d.%m.%Y"
        )
        global_week_map_list = database["semester_info"]["global_week_map"]
        self.week_tuple_to_idx = {
            tuple(week): i for i, week in enumerate(global_week_map_list)
        }
        # ИЗМЕНЕНО: Добавляем map для групп для быстрого поиска ID по имени
        self.group_name_to_id = {
            name: i for i, name in enumerate(self.db["dictionaries"]["groups"])
        }

    def _get_week_id_for_date(
        self, group_id: int, target_date: datetime
    ) -> int | None:
        # ИЗМЕНЕНО: Получение расписания по ID группы из массива
        if not (0 <= group_id < len(self.db["schedules"])):
            return None
        schedule = self.db["schedules"][group_id]
        if not schedule:
            return None
        year, week_num, _ = target_date.isocalendar()
        global_idx = self.week_tuple_to_idx.get((year, week_num))
        if global_idx is None:
            return None
        week_id = schedule[global_idx]
        return week_id if week_id > 0 else None

    def get_lessons_for_auditorium(
        self, date_str: str, time_str: str, room_name: str
    ) -> list:
        found_lessons = []
        try:
            target_date = datetime.strptime(date_str, "%d.%m.%Y")
            target_weekday_idx = target_date.weekday()
            if target_weekday_idx == 6:
                return []
            pair_idx = TIME_STR_TO_PAIR_IDX.get(time_str)
            if pair_idx is None:
                return []
            target_room_id = self.db["dictionaries"]["rooms"].index(room_name)

            # ИЗМЕНЕНО: relevant_groups теперь содержит ID, а не имена
            relevant_group_ids = self.db["inverted_indices"]["rooms"].get(
                str(target_room_id), []
            )
            if not relevant_group_ids:
                return []
        except (ValueError, IndexError):
            return []

        for group_id in relevant_group_ids:
            week_id = self._get_week_id_for_date(group_id, target_date)
            if week_id is None:
                continue
            week_pattern = self.db["meta_patterns"]["weeks"][week_id]
            if not week_pattern:
                continue
            day_id = week_pattern[target_weekday_idx]
            if not day_id:
                continue
            day_pattern = self.db["meta_patterns"]["days"][day_id]
            if not day_pattern:
                continue
            lesson_id = day_pattern[pair_idx]
            if lesson_id:
                lesson_data = Lesson(
                    *self.db["meta_patterns"]["lessons"][lesson_id]
                )
                if lesson_data.room_id == target_room_id:
                    # ИЗМЕНЕНО: Имя группы получаем из словаря по ID
                    group_name = self.db["dictionaries"]["groups"][group_id]
                    found_lessons.append({
                        "group": group_name,
                        "subject": self.db["dictionaries"]["subjects"][
                            lesson_data.subject_id
                        ],
                        "lector": self.db["dictionaries"]["lectors"][
                            lesson_data.lector_id
                        ],
                        "type": self.db["dictionaries"]["types"][
                            lesson_data.type_id
                        ],
                    })
        return found_lessons

    def get_schedule_for_lector(self, lector_name: str) -> dict | None:
        match = re.match(r"^(.*?)\s*(\([^)]+\))?$", lector_name)
        if not match:
            main_name = lector_name.strip().title()
            suffix = ""
        else:
            main_name = match.group(1).strip().title()
            suffix = (match.group(2) or "").lower()
        normalized_name = (main_name + " " + suffix).strip()

        try:
            lector_id = self.db["dictionaries"]["lectors"].index(
                normalized_name
            )
        except ValueError:
            return None

        # ИЗМЕНЕНО: relevant_groups теперь содержит ID
        relevant_group_ids = self.db["inverted_indices"]["lectors"].get(
            str(lector_id), []
        )
        if not relevant_group_ids:
            return {}

        aggregated_lessons = defaultdict(set)
        global_week_map = self.db["semester_info"]["global_week_map"]

        for year, week_num in global_week_map:
            for day_offset in range(6):
                current_date = datetime.fromisocalendar(
                    year, week_num, 1
                ) + timedelta(days=day_offset)
                date_str = current_date.strftime("%d.%m.%Y")

                for group_id in relevant_group_ids:
                    week_id = self._get_week_id_for_date(
                        group_id, current_date
                    )
                    if not week_id:
                        continue
                    week_pattern = self.db["meta_patterns"]["weeks"][week_id]
                    day_id = week_pattern[day_offset]
                    if not day_id:
                        continue
                    day_pattern = self.db["meta_patterns"]["days"][day_id]
                    for pair_idx, lesson_id in enumerate(day_pattern):
                        if not lesson_id:
                            continue
                        lesson_data = Lesson(
                            *self.db["meta_patterns"]["lessons"][lesson_id]
                        )
                        if lesson_data.lector_id == lector_id:
                            lesson_key = (
                                date_str,
                                pair_idx,
                                lesson_data.subject_id,
                                lesson_data.type_id,
                                lesson_data.room_id,
                            )
                            # ИЗМЕНЕНО: Сохраняем group_id
                            aggregated_lessons[lesson_key].add(group_id)

        final_schedule = defaultdict(list)
        groups_dict = self.db["dictionaries"]["groups"]
        for lesson_key, group_ids in aggregated_lessons.items():
            date_str, pair_idx, subject_id, type_id, room_id = lesson_key
            pair_num = pair_idx + 1
            time_str = PAIR_NUM_TO_TIME[pair_num]

            # ИЗМЕНЕНО: Преобразуем ID групп в имена и сортируем
            group_names = sorted([groups_dict[gid] for gid in group_ids])

            final_schedule[date_str].append({
                "pair_num": pair_num,
                "pair_time_str": f"Пара {pair_num} ({time_str})",
                "groups": group_names,
                "subject": self.db["dictionaries"]["subjects"][subject_id],
                "type": self.db["dictionaries"]["types"][type_id],
                "room": self.db["dictionaries"]["rooms"][room_id],
            })

        for date_str in final_schedule:
            final_schedule[date_str].sort(key=lambda x: x["pair_num"])

        return dict(
            sorted(
                final_schedule.items(),
                key=lambda item: datetime.strptime(item[0], "%d.%m.%Y"),
            )
        )

    def iter_all_lessons(self) -> Iterator[tuple[str, str, str, str]]:
        # ИЗМЕНЕНО: Полностью переписан для эффективности с новой структурой
        global_week_map_list = self.db["semester_info"]["global_week_map"]
        groups_list = self.db["dictionaries"]["groups"]
        lessons_meta = self.db["meta_patterns"]["lessons"]
        days_meta = self.db["meta_patterns"]["days"]
        weeks_meta = self.db["meta_patterns"]["weeks"]
        rooms_list = self.db["dictionaries"]["rooms"]

        for group_id, group_schedule in enumerate(self.db["schedules"]):
            if not group_schedule:
                continue
            group_name = groups_list[group_id]

            for global_week_idx, week_id in enumerate(group_schedule):
                if not week_id:
                    continue
                year, week_num = global_week_map_list[global_week_idx]
                week_pattern = weeks_meta[week_id]

                for weekday_idx, day_id in enumerate(week_pattern):
                    if not day_id:
                        continue
                    current_date = datetime.fromisocalendar(
                        year, week_num, 1
                    ) + timedelta(days=weekday_idx)
                    date_str = current_date.strftime("%d.%m.%Y")
                    day_pattern = days_meta[day_id]

                    for pair_idx, lesson_id in enumerate(day_pattern):
                        if lesson_id:
                            time_str = PAIR_NUM_TO_TIME[pair_idx + 1]
                            lesson = Lesson(*lessons_meta[lesson_id])
                            room_name = rooms_list[lesson.room_id]
                            if room_name:
                                yield group_name, date_str, time_str, room_name


# ... (остальные функции _load_and_flatten_schedule, load_schedules_parallel, и т.д. остаются без изменений)
# ... (Я их опущу для краткости, так как они не меняются)
def _load_and_flatten_schedule(file_path: Path) -> list[dict]:
    """Читает один JSON и уплощает его в список занятий."""
    lessons = []
    try:
        with file_path.open("rb") as f:
            data: dict[str, dict[str, dict]] = orjson.loads(f.read())
        group = data.get("group")
        if not group:
            return []
        for date_str, day_info in data.items():
            if not isinstance(day_info, dict) or "pairs" not in day_info:
                continue
            for time_start, pairs in day_info["pairs"].items():
                pairs: dict[str, dict[str, dict]]
                pair_idx = TIME_STR_TO_PAIR_IDX.get(time_start)
                if pair_idx is None:
                    continue
                subject, details = next(iter(pairs.items()), (None, None))
                if not subject or not details:
                    continue
                lessons.append({
                    "group": group,
                    "date": date_str,
                    "pair_idx": pair_idx,
                    "subject": subject,
                    "lector": next(
                        iter(details.get("lector", {}).values()), None
                    ),
                    "type": next(iter(details.get("type", {}).keys()), None),
                    "room": next(iter(details.get("room", {}).values()), None),
                })
    except Exception:
        return []
    return lessons


def load_schedules_parallel() -> pd.DataFrame:
    if not LOCAL_SCHEDULES_DIR.exists():
        raise FileNotFoundError(f"Папка '{LOCAL_SCHEDULES_DIR}' не найдена.")

    schedule_files = list(LOCAL_SCHEDULES_DIR.rglob("*.json"))
    if not schedule_files:
        raise FileNotFoundError("ОШИБКА: Файлы .json не найдены.")

    all_lessons = []
    with ThreadPoolExecutor() as executor:
        future_to_file = {
            executor.submit(_load_and_flatten_schedule, fp): fp
            for fp in schedule_files
        }
        for future in as_completed(future_to_file):
            all_lessons.extend(future.result())

    return pd.DataFrame(all_lessons)


def create_compressed_schedule_database() -> None:
    all_lessons_df = load_schedules_parallel()
    if all_lessons_df.empty:
        print("Нет данных для обработки. База данных не создана.")
        return

    compressor = HybridCompressor()
    schedules = compressor.process_schedules_dataframe(all_lessons_df)
    database = compressor.get_database(schedules)

    with COMPRESSED_SCHEDULE_FILE.open("wb") as f:
        f.write(orjson.dumps(database))
    print(f"База данных успешно создана: {COMPRESSED_SCHEDULE_FILE}")


def analyze_and_find_free_rooms() -> None:
    if not COMPRESSED_SCHEDULE_FILE.exists():
        print("Сначала создайте сжатую базу данных.")
        return

    with COMPRESSED_SCHEDULE_FILE.open("rb") as f:
        database = orjson.loads(f.read())

    decompressor = HybridDecompressor(database)
    all_rooms = {
        room
        for room in decompressor.db["dictionaries"].get("rooms", [])
        if room
    }

    busy_schedule = defaultdict(lambda: defaultdict(set))
    for _, date, time_str, room_name in decompressor.iter_all_lessons():
        busy_schedule[date][time_str].add(room_name)

    free_auditoriums = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    all_dates = sorted(busy_schedule.keys())

    for date in all_dates:
        for time_start, pair_num in PAIR_TIMES_TO_NUM.items():
            free_at_time = all_rooms - busy_schedule[date].get(
                time_start, set()
            )
            rooms_by_building = defaultdict(list)
            for room in sorted(free_at_time):
                building = (
                    room.split("-", 1)[0].strip()
                    if "-" in room and not room.startswith("--")
                    else "Другое"
                )
                rooms_by_building[building].append(room)
            for building, rooms in sorted(rooms_by_building.items()):
                if rooms:
                    free_auditoriums[date][f"Пара {pair_num} ({time_start})"][
                        building
                    ] = rooms


def check_auditorium_status(
    date_str: str, pair_number: int, room_name: str
) -> None | tuple[int | str, ...] | Literal[False]:
    if not COMPRESSED_SCHEDULE_FILE.exists():
        raise FileNotFoundError(
            f"Файл '{COMPRESSED_SCHEDULE_FILE.name}' не найден."
        )

    time_start_str = PAIR_NUM_TO_TIME.get(pair_number)
    if not time_start_str:
        raise ValueError(f"Неверный номер пары: {pair_number}.")

    with COMPRESSED_SCHEDULE_FILE.open("rb") as f:
        database = orjson.loads(f.read())

    decompressor = HybridDecompressor(database)
    if room_name not in decompressor.db["dictionaries"].get("rooms", []):
        return None

    found_lessons = decompressor.get_lessons_for_auditorium(
        date_str, time_start_str, room_name
    )
    if not found_lessons:
        return False

    return tuple(
        (
            i,
            lesson["group"],
            lesson["subject"],
            lesson["type"],
            lesson["lector"],
        )
        for i, lesson in enumerate(found_lessons, 1)
    )


def get_lector_schedule(lector_name: str) -> dict | None:
    """
    Возвращает полное расписание для указанного преподавателя.

    Args:
        lector_name: Имя преподавателя для поиска.

    Returns:
        Словарь с расписанием или None, если преподаватель не найден.
    """
    if not COMPRESSED_SCHEDULE_FILE.exists():
        raise FileNotFoundError(
            f"Файл '{COMPRESSED_SCHEDULE_FILE.name}' не найден."
        )

    with COMPRESSED_SCHEDULE_FILE.open("rb") as f:
        database = orjson.loads(f.read())

    decompressor = HybridDecompressor(database)
    return decompressor.get_schedule_for_lector(lector_name)


if __name__ == "__main__":
    # Шаг 1: Создание/обновление сжатой базы данных
    print("--- Создание сжатой базы данных ---")
    create_compressed_schedule_database()
    print("-" * 20)

    # Шаг 2: Анализ и поиск свободных аудиторий
    print("\n--- Анализ и поиск свободных аудиторий ---")
    analyze_and_find_free_rooms()
    print("-" * 20)

    # Шаг 3: Демонстрация новой функции - расписание преподавателя
    print("\n--- Демонстрация расписания преподавателя ---")
    # Замените на реальное имя преподавателя из ваших данных
    TARGET_LECTOR = "Земсков Андрей Владимирович"
    print(f"Поиск расписания для: {TARGET_LECTOR}")

    schedule = get_lector_schedule(TARGET_LECTOR)

    if schedule is None:
        print(f"Преподаватель '{TARGET_LECTOR}' не найден в базе данных.")
    elif not schedule:
        print(
            f"У преподавателя '{TARGET_LECTOR}' нет запланированных занятий."
        )
    else:
        print(f"Расписание для '{TARGET_LECTOR}':")

        for date, lessons_on_day in schedule.items():
            print(f"\n[ {date} ]")
            for lesson in lessons_on_day:
                print(
                    f"  {lesson['pair_time_str']}: {lesson['subject']} ({lesson['type']})"
                )
                # Выводим список групп через запятую
                groups_str = ", ".join(lesson["groups"])
                print(f"    Группы: {groups_str}, Аудитория: {lesson['room']}")
