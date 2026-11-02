"""The Summit Air world: about 300 customers, 40 technicians, two weeks of capacity.

Deterministic: the random seed is fixed, and everything is placed relative to the start date, so
the same start date always gives the same world. Names, addresses and 555 phone numbers are
invented. ZIP codes are real ZIPs in the configured counties; the streets are common NYC street
names with made-up house numbers.
"""

import json
import random
import sqlite3
from datetime import date, datetime, timedelta

from servicetitan_mock import capacity, repo
from servicetitan_mock import worldkit as wk
from servicetitan_mock.config import TenantConfig, iso
from servicetitan_mock.util import phone_key

SEED = 20261102
CUSTOMER_TOTAL = 300
SERVICE_UNIT, INSTALL_UNIT = 11, 12
NO_HEAT, NO_COOLING, TUNE_UP, SAFETY_CHECK, ESTIMATE = 201, 202, 203, 204, 205
PREMIUM, STANDARD, LEGACY = 601, 602, 603
FIRST_TECH_ID = 5001
SERVICE_TECHS, ADVISORS = 36, 4

AREA_CODES = ["718", "347", "929", "917", "646", "212"]
CITY_BY_COUNTY = {"Kings": "Brooklyn", "Queens": "Queens", "Richmond": "Staten Island"}

FIRST = [
    "Maria",
    "James",
    "Linda",
    "Robert",
    "Aisha",
    "Wei",
    "Carlos",
    "Priya",
    "Michael",
    "Olga",
    "Dennis",
    "Fatima",
    "Joseph",
    "Angela",
    "Tyrone",
    "Yuki",
    "Samuel",
    "Rosa",
    "Andrei",
    "Keisha",
    "Anthony",
    "Mei",
    "Luis",
    "Deborah",
    "Hassan",
    "Nicole",
    "Victor",
    "Grace",
    "Omar",
    "Patricia",
    "Daniel",
    "Svetlana",
    "Marcus",
    "Elena",
    "Jamal",
    "Kimberly",
    "Paul",
    "Ananya",
    "George",
    "Tamika",
]
LAST = [
    "Rivera",
    "Chen",
    "Kowalski",
    "Johnson",
    "Patel",
    "Rodriguez",
    "Nguyen",
    "Cohen",
    "Williams",
    "Ahmed",
    "Russo",
    "Kim",
    "Brown",
    "Singh",
    "Garcia",
    "Ivanov",
    "Jackson",
    "Lopez",
    "Murphy",
    "Hernandez",
    "Greenberg",
    "Park",
    "Thomas",
    "Alvarez",
    "Bianchi",
    "Okoye",
    "Martinez",
    "Levy",
    "Davis",
    "Haddad",
    "Romano",
    "Wong",
    "Santiago",
    "Fitzgerald",
    "Mensah",
    "Petrov",
    "Baker",
    "Diaz",
]
STREETS = {
    "Kings": [
        "Bedford Ave",
        "Nostrand Ave",
        "Flatbush Ave",
        "Ocean Ave",
        "Union St",
        "Lexington Ave",
        "Avenue J",
        "Avenue U",
        "Coney Island Ave",
        "Dekalb Ave",
        "Fenimore St",
        "Church Ave",
    ],
    "Queens": [
        "Northern Blvd",
        "Jamaica Ave",
        "Woodhaven Blvd",
        "Astoria Blvd",
        "Union Tpke",
        "Metropolitan Ave",
        "Springfield Blvd",
        "Francis Lewis Blvd",
        "Ditmars Blvd",
        "34th Ave",
    ],
    "Richmond": [
        "Forest Ave",
        "Victory Blvd",
        "Hylan Blvd",
        "Richmond Ave",
        "Amboy Rd",
        "Bay St",
        "Arden Ave",
        "Annadale Rd",
        "Todt Hill Rd",
        "Castleton Ave",
    ],
}
BOILER_BRANDS = ["Burnham", "Weil-McLain", "Peerless", "Slant/Fin", "Utica"]
FURNACE_BRANDS = ["Carrier", "Trane", "Lennox", "Goodman", "Rheem"]
AC_BRANDS = ["Carrier", "Trane", "Lennox", "Goodman"]
SPLIT_BRANDS = ["Mitsubishi", "Daikin", "Fujitsu"]
CALL_REASONS = [
    "No heat",
    "No cooling",
    "Tune-up",
    "Quote for new system",
    "Question about my visit",
]

# Offsets from the start date, and how many normal slots stay open per window (the urgent hold
# stays on top of that).
NEARLY_FULL = {
    2: {"morning": 0, "afternoon": 0, "evening": 0},
    3: {"morning": 0, "afternoon": 1, "evening": 0},
    9: {"morning": 1, "afternoon": 1, "evening": 2},
}
DAYS_OF_CAPACITY = 14

# Fixed numbers for the named scenarios.
DELGADO_PHONE = "(718) 555-0142"
BRENNAN_PHONE = "(347) 555-0177"
PARK_PHONE = "(718) 555-0123"
DAVID_PARK_PHONE = "(917) 555-0168"
MORETTI_PHONE = "(718) 555-0131"
SANTOS_PHONE = "(929) 555-0114"
OKAFOR_PHONE = "(718) 555-0156"
RUIZ_PHONE = "(718) 555-0119"
OCASIO_PHONE = "(347) 555-0162"
NAIR_PHONE = "(347) 555-0173"
WHITMAN_PHONE = "(917) 555-0150"
BAKERY_PHONE = "(718) 555-0188"
FELD_PHONE = "(929) 555-0105"
RESERVED = {
    DELGADO_PHONE,
    BRENNAN_PHONE,
    PARK_PHONE,
    DAVID_PARK_PHONE,
    MORETTI_PHONE,
    SANTOS_PHONE,
    OKAFOR_PHONE,
    RUIZ_PHONE,
    OCASIO_PHONE,
    NAIR_PHONE,
    WHITMAN_PHONE,
    BAKERY_PHONE,
    FELD_PHONE,
}


class World:
    def __init__(self, conn: sqlite3.Connection, cfg: TenantConfig, start: date):
        self.conn, self.cfg, self.start = conn, cfg, start
        self.rng = random.Random(SEED)
        numbers = [f"({a}) 555-{n:04d}" for a in AREA_CODES for n in range(100, 200)]
        self.phones = [n for n in numbers if n not in RESERVED]
        self.rng.shuffle(self.phones)
        self.people: list[dict] = []
        self.customers = 0
        self.scenarios: dict = {}
        self.today = start
        self.work_day = start if start.weekday() < 6 else start + timedelta(days=1)
        self.service_techs: list[int] = []
        self.advisors: list[int] = []

    # ---- small helpers --------------------------------------------------------------------

    def stamp(self, days_ago: int) -> datetime:
        return wk.local_dt(self.cfg, self.start - timedelta(days=days_ago), "09:00")

    def phone(self) -> str:
        return self.phones.pop()

    def name(self) -> str:
        return f"{self.rng.choice(FIRST)} {self.rng.choice(LAST)}"

    def address(self, zone: dict | None = None, unit: str = "") -> dict:
        zone = zone or self.rng.choice(self.cfg.zones)
        return {
            "street": f"{self.rng.randint(11, 989)} {self.rng.choice(STREETS[zone['county']])}",
            "unit": unit,
            "city": CITY_BY_COUNTY[zone["county"]],
            "state": "NY",
            "zip": self.rng.choice(zone["zips"]),
        }

    def add_person(
        self,
        name: str,
        address: dict,
        role: str,
        phones: list[str],
        kind: str = "Residential",
        days_ago: int | None = None,
        extra: list[dict] | None = None,
    ) -> tuple[int, int]:
        when = self.stamp(days_ago if days_ago is not None else self.rng.randint(30, 1200))
        customer_id = repo.insert_customer(self.conn, when, name, kind)
        location_id = repo.insert_location(
            self.conn, self.cfg, when, customer_id, address["street"], address
        )
        for i, number in enumerate(phones):
            repo.insert_contact(
                self.conn,
                when,
                customer_id,
                {
                    "type": "MobilePhone" if i == 0 else "Phone",
                    "value": number,
                    "role": role,
                    "name": name,
                },
            )
        email = name.lower().replace(" ", ".").replace("'", "") + "@example.com"
        repo.insert_contact(
            self.conn, when, customer_id, {"type": "Email", "value": email, "role": role}
        )
        for contact in extra or []:
            repo.insert_contact(self.conn, when, customer_id, contact)
        self.customers += 1
        return customer_id, location_id

    def equipment(self, customer_id: int, location_id: int, kind: str) -> None:
        rng = self.rng

        def install(system: str, brands: list[str], prefix: str) -> None:
            brand = rng.choice(brands)
            installed = date(
                rng.randint(1996, self.start.year - 1), rng.randint(1, 12), rng.randint(1, 28)
            )
            wk.add_equipment(
                self.conn,
                self.cfg,
                customer_id,
                location_id,
                system,
                brand,
                f"{prefix}-{rng.randint(1000, 9999)}",
                installed,
            )

        if kind == "house":
            if rng.random() < 0.6:
                install("boiler", BOILER_BRANDS, "B")
            else:
                install("furnace", FURNACE_BRANDS, "F")
            roll = rng.random()
            if roll < 0.5:
                install("central_ac", AC_BRANDS, "CA")
            elif roll < 0.7:
                install("ductless_mini_split", SPLIT_BRANDS, "MS")
            elif roll < 0.85:
                install("heat_pump", SPLIT_BRANDS, "HP")
        elif kind == "apartment":
            install(
                "ductless_mini_split" if rng.random() < 0.6 else "heat_pump", SPLIT_BRANDS, "MS"
            )

    def membership(self, customer_id: int, location_id: int, type_id: int, status: str) -> int:
        rng, today = self.rng, self.today
        if status == "Active":
            end = today + timedelta(days=rng.randint(20, 340))
        elif status == "Expired":
            end = today - timedelta(days=rng.randint(15, 400))
        else:
            end = today + timedelta(days=rng.randint(-200, 200))
        start = end - timedelta(days=365)
        return wk.add_membership(
            self.conn, self.cfg, customer_id, location_id, type_id, status, start, end, today
        )

    def maybe_member(self, customer_id: int, location_id: int, chance: float) -> None:
        if self.rng.random() >= chance:
            return
        status = self.rng.choices(["Active", "Expired", "Canceled"], [65, 22, 13])[0]
        types = [PREMIUM, STANDARD] + ([LEGACY] if status != "Active" else [])
        weights = [55, 40, 5][: len(types)]
        self.membership(customer_id, location_id, self.rng.choices(types, weights)[0], status)

    # ---- technicians and shifts -----------------------------------------------------------

    def technicians(self) -> None:
        rng, cfg = self.rng, self.cfg
        names = set()
        while len(names) < SERVICE_TECHS + ADVISORS:
            names.add(self.name())
        names = sorted(names)
        rng.shuffle(names)
        for i in range(SERVICE_TECHS + ADVISORS):
            tech_id = FIRST_TECH_ID + i
            phone = f"(917) 555-{200 + i:04d}"
            zone_id = cfg.zones[i % len(cfg.zones)]["id"]
            if i < SERVICE_TECHS:
                skills = ["maintenance"]
                if i % 6 != 0:
                    skills.append("heating")
                if i % 5 != 0:
                    skills.append("cooling")
                if "heating" not in skills and "cooling" not in skills:
                    skills.append("heating")
                if i % 4 == 0:
                    skills.append("heat_pump")
                wk.add_technician(
                    self.conn, self.cfg, tech_id, names[i], SERVICE_UNIT, zone_id, skills, phone
                )
                self.service_techs.append(tech_id)
            else:
                wk.add_technician(
                    self.conn,
                    self.cfg,
                    tech_id,
                    names[i] + " (comfort advisor)",
                    INSTALL_UNIT,
                    zone_id,
                    [],
                    phone,
                )
                self.advisors.append(tech_id)

    def shifts(self) -> None:
        rng, cfg, conn = self.rng, self.cfg, self.conn
        days = [self.start + timedelta(days=d) for d in range(DAYS_OF_CAPACITY)]
        all_skilled = [
            t
            for t in self.service_techs
            if {"heating", "cooling"}
            <= set(
                json.loads(
                    conn.execute(
                        "SELECT skills_json FROM technicians WHERE id = ?", (t,)
                    ).fetchone()[0]
                )
            )
        ]
        for day in days:
            for i, tech_id in enumerate(self.service_techs):
                if day.weekday() == 6 or (day.weekday() < 5 and rng.random() < 0.05):
                    continue
                if day.weekday() == 5:
                    if i % 3 == 0:
                        wk.add_shift(conn, cfg, tech_id, day, "08:00", "16:00")
                    continue
                group = i % 10
                start, end = (
                    ("08:00", "16:00")
                    if group < 4
                    else ("12:00", "20:00")
                    if group < 7
                    else ("08:00", "20:00")
                )
                wk.add_shift(conn, cfg, tech_id, day, start, end)
            if day.weekday() < 6:
                for i, tech_id in enumerate(self.advisors):
                    if day.weekday() < 5 or i < 2:
                        wk.add_shift(conn, cfg, tech_id, day, "08:00", "16:00")
            # One technician carries the phone for the whole Monday-to-Sunday week.
            week = (day - timedelta(days=day.weekday())).toordinal() // 7
            on_call = all_skilled[week % len(all_skilled)]
            wk.add_shift(
                conn,
                cfg,
                on_call,
                day,
                "20:00",
                "08:00",
                "OnCall",
                ends_next_day=True,
                note="Weekly on-call",
            )
        wk.add_holds(conn, cfg, days)
        self.scenarios["onCall"] = {
            day.isoformat(): all_skilled[
                ((day - timedelta(days=day.weekday())).toordinal() // 7) % len(all_skilled)
            ]
            for day in days
        }

    # ---- customers ------------------------------------------------------------------------

    def named_customers(self) -> None:
        s, cfg = self.scenarios, self.cfg
        zone = {z["id"]: z for z in cfg.zones}

        cid, lid = self.add_person("Maria Delgado", self.address(zone[2]), "owner", [DELGADO_PHONE])
        self.equipment(cid, lid, "house")
        job_id, appt_id = wk.add_job(
            self.conn,
            cfg,
            cid,
            lid,
            NO_HEAT,
            self.work_day,
            "afternoon",
            "Dispatched",
            "Urgent",
            summary="No heat since last night. Radiators cold, thermostat reads 58.",
            created=wk.local_dt(cfg, self.start, "08:15"),
        )
        for hhmm, link in (("08:12", False), ("08:31", True), ("08:47", True)):
            wk.add_call(
                self.conn,
                cfg,
                wk.local_dt(cfg, self.start, hhmm),
                DELGADO_PHONE,
                cid,
                job_id if link else None,
                "Booked" if link else "Unbooked",
                "No heat",
                240 if link else 35,
            )
        s["repeatCallerOpenJob"] = {
            "name": "Maria Delgado",
            "phone": DELGADO_PHONE,
            "customerId": cid,
            "locationId": lid,
            "jobId": job_id,
            "appointmentId": appt_id,
        }

        cid, lid = self.add_person(
            "Thomas Brennan", self.address(zone[3]), "owner", [BRENNAN_PHONE]
        )
        self.equipment(cid, lid, "house")
        done_day = self.start - timedelta(days=6)
        job_id, _ = wk.add_job(
            self.conn,
            cfg,
            cid,
            lid,
            NO_COOLING,
            done_day,
            "morning",
            "Done",
            tech_id=self.service_techs[3],
            summary="AC not cooling. Replaced capacitor.",
            created=wk.local_dt(cfg, done_day - timedelta(days=1), "10:00"),
        )
        wk.add_call(
            self.conn,
            cfg,
            wk.local_dt(cfg, done_day - timedelta(days=1), "10:05"),
            BRENNAN_PHONE,
            cid,
            job_id,
            "Booked",
            "No cooling",
        )
        s["jobDoneLastWeek"] = {
            "name": "Thomas Brennan",
            "phone": BRENNAN_PHONE,
            "customerId": cid,
            "locationId": lid,
            "jobId": job_id,
        }

        cid, lid = self.add_person(
            "Linda Park",
            self.address(zone[4]),
            "owner",
            [PARK_PHONE],
            extra=[
                {
                    "type": "MobilePhone",
                    "value": DAVID_PARK_PHONE,
                    "role": "adult_child",
                    "name": "David Park",
                }
            ],
        )
        self.equipment(cid, lid, "house")
        member_id = self.membership(cid, lid, PREMIUM, "Active")
        s["activeMemberWithAdultChild"] = {
            "name": "Linda Park",
            "phone": PARK_PHONE,
            "adultChildPhone": DAVID_PARK_PHONE,
            "customerId": cid,
            "locationId": lid,
            "membershipId": member_id,
        }

        cid, lid = self.add_person("Frank Moretti", self.address(zone[2]), "owner", [MORETTI_PHONE])
        self.equipment(cid, lid, "house")
        end = self.start - timedelta(days=40)
        member_id = wk.add_membership(
            self.conn,
            cfg,
            cid,
            lid,
            PREMIUM,
            "Expired",
            end - timedelta(days=365),
            end,
            self.today,
        )
        s["expiredMember"] = {
            "name": "Frank Moretti",
            "phone": MORETTI_PHONE,
            "customerId": cid,
            "locationId": lid,
            "membershipId": member_id,
        }

        cid, lid = self.add_person("Gloria Santos", self.address(zone[1]), "owner", [SANTOS_PHONE])
        self.equipment(cid, lid, "house")
        end = self.start - timedelta(days=1)
        member_id = wk.add_membership(
            self.conn,
            cfg,
            cid,
            lid,
            STANDARD,
            "Active",
            end - timedelta(days=365),
            end,
            self.today,
        )
        s["storedActiveButLapsed"] = {
            "name": "Gloria Santos",
            "phone": SANTOS_PHONE,
            "customerId": cid,
            "locationId": lid,
            "membershipId": member_id,
        }

        cid, lid = self.add_person("Raymond Okafor", self.address(zone[4]), "owner", [OKAFOR_PHONE])
        self.equipment(cid, lid, "house")
        first = self.membership(cid, lid, PREMIUM, "Active")
        second = self.membership(cid, lid, STANDARD, "Active")
        s["twoPlansOneAddress"] = {
            "name": "Raymond Okafor",
            "phone": OKAFOR_PHONE,
            "customerId": cid,
            "locationId": lid,
            "membershipIds": [first, second],
        }

        address = self.address(zone[2])
        first_id, first_loc = self.add_person("Hector Ruiz", address, "owner", [RUIZ_PHONE])
        second_id, _ = self.add_person("Marisol Ruiz", address, "owner", [RUIZ_PHONE])
        self.equipment(first_id, first_loc, "house")
        s["sharedPhone"] = {
            "phone": RUIZ_PHONE,
            "customerIds": [first_id, second_id],
            "street": address["street"],
            "zip": address["zip"],
        }

        address = self.address(zone[1])
        owner_id, owner_loc = self.add_person("Dennis Ocasio", address, "owner", [OCASIO_PHONE])
        self.equipment(owner_id, owner_loc, "house")
        tenant_id, _ = self.add_person(
            "Priya Nair", {**address, "unit": "2"}, "tenant", [NAIR_PHONE]
        )
        s["ownerAndTenant"] = {
            "street": address["street"],
            "zip": address["zip"],
            "ownerCustomerId": owner_id,
            "ownerPhone": OCASIO_PHONE,
            "tenantCustomerId": tenant_id,
            "tenantPhone": NAIR_PHONE,
            "tenantUnit": "2",
        }

        cid, lid = self.add_person(
            "Gerald Whitman",
            {
                "street": "47 Willis Ave",
                "unit": "",
                "city": "Mineola",
                "state": "NY",
                "zip": "11501",
            },
            "owner",
            [WHITMAN_PHONE],
        )
        s["outOfArea"] = {
            "name": "Gerald Whitman",
            "phone": WHITMAN_PHONE,
            "customerId": cid,
            "locationId": lid,
            "zip": "11501",
        }

        cid, lid = self.add_person(
            "Harbor Bakery Group", self.address(zone[1]), "owner", [BAKERY_PHONE], kind="Commercial"
        )
        self.conn.execute(
            """INSERT INTO leads (kind, customer_type, customer_id, name, phone, phone_key, street, zip, summary,
                                  created_on) VALUES ('Commercial', 'Commercial', ?, 'Harbor Bakery Group', ?, ?,
                                  '', '', 'Rooftop unit over the ovens stopped cooling. Wants a quote for service.', ?)""",
            (cid, BAKERY_PHONE, phone_key(BAKERY_PHONE), iso(self.stamp(4))),
        )
        s["commercial"] = {
            "name": "Harbor Bakery Group",
            "phone": BAKERY_PHONE,
            "customerId": cid,
            "locationId": lid,
        }

        self.conn.execute(
            """INSERT INTO leads (kind, customer_type, name, phone, phone_key, street, zip, summary, created_on)
               VALUES ('Replacement', 'Residential', 'Naomi Feld', ?, ?, '212 Dahill Rd', '11204',
                       'Oil boiler is 30 years old. Wants a heat pump quote.', ?)""",
            (FELD_PHONE, phone_key(FELD_PHONE), iso(self.stamp(2))),
        )
        s["replacementLead"] = {"name": "Naomi Feld", "phone": FELD_PHONE}

    def random_customers(self) -> None:
        rng = self.rng
        while self.customers < CUSTOMER_TOTAL:
            zone = rng.choice(self.cfg.zones)
            roll = rng.random()
            if roll < 0.60:
                name, address = self.name(), self.address(zone)
                phones = [self.phone()] + ([self.phone()] if rng.random() < 0.2 else [])
                extra = []
                if rng.random() < 0.08:
                    extra = [
                        {
                            "type": "MobilePhone",
                            "value": self.phone(),
                            "role": "adult_child",
                            "name": self.name(),
                        }
                    ]
                cid, lid = self.add_person(name, address, "owner", phones, extra=extra)
                self.equipment(cid, lid, "house")
                self.maybe_member(cid, lid, 0.40)
                self.people.append({"cid": cid, "lid": lid, "phone": phones[0], "zone": zone})
                if rng.random() < 0.3 and self.customers < CUSTOMER_TOTAL:
                    phone = self.phone()
                    tid, tloc = self.add_person(
                        self.name(),
                        {**address, "unit": rng.choice(["2", "2R", "3"])},
                        "tenant",
                        [phone],
                    )
                    self.people.append(
                        {"cid": tid, "lid": tloc, "phone": phone, "zone": zone, "tenant": True}
                    )
            elif roll < 0.82:
                phone = self.phone()
                cid, lid = self.add_person(
                    self.name(),
                    self.address(zone, unit=f"{rng.randint(1, 9)}{rng.choice('ABCDEF')}"),
                    "owner",
                    [phone],
                )
                self.equipment(cid, lid, "apartment")
                self.maybe_member(cid, lid, 0.20)
                self.people.append({"cid": cid, "lid": lid, "phone": phone, "zone": zone})
            else:
                phone = self.phone()
                cid, lid = self.add_person(
                    self.name(),
                    self.address(zone, unit=f"{rng.randint(2, 4)}{rng.choice('FRL')}"),
                    "tenant",
                    [phone],
                )
                self.people.append(
                    {"cid": cid, "lid": lid, "phone": phone, "zone": zone, "tenant": True}
                )

    # ---- history, calls and bookings ------------------------------------------------------

    def past_jobs_and_calls(self) -> None:
        rng, conn, cfg = self.rng, self.conn, self.cfg
        kinds = [(TUNE_UP, 40), (NO_HEAT, 25), (NO_COOLING, 25), (SAFETY_CHECK, 10)]
        for p in self.people:
            if not p.get("tenant") and rng.random() < 0.25:
                for _ in range(rng.randint(1, 3)):
                    job_type = rng.choices([k for k, _ in kinds], [w for _, w in kinds])[0]
                    for _attempt in range(20):
                        day = self.start - timedelta(days=rng.randint(14, 700))
                        try:
                            wk.add_job(
                                conn,
                                cfg,
                                p["cid"],
                                p["lid"],
                                job_type,
                                day,
                                rng.choice(["morning", "afternoon", "evening"]),
                                "Done",
                                tech_id=rng.choice(self.service_techs),
                                summary="Completed visit.",
                                created=wk.local_dt(cfg, day - timedelta(days=2), "11:00"),
                            )
                            break
                        except sqlite3.IntegrityError:
                            continue
            if rng.random() < 0.5:
                for _ in range(rng.randint(1, 3)):
                    when = wk.local_dt(
                        cfg,
                        self.start - timedelta(days=rng.randint(2, 90)),
                        f"{rng.randint(8, 19):02d}:{rng.randint(0, 59):02d}",
                    )
                    wk.add_call(
                        conn,
                        cfg,
                        when,
                        p["phone"],
                        p["cid"],
                        None,
                        rng.choice(["Booked", "Unbooked"]),
                        rng.choice(CALL_REASONS),
                    )
        for _ in range(25):
            when = wk.local_dt(
                cfg,
                self.start - timedelta(days=rng.randint(1, 90)),
                f"{rng.randint(8, 19):02d}:{rng.randint(0, 59):02d}",
            )
            wk.add_call(
                conn, cfg, when, self.phone(), None, None, "Unbooked", rng.choice(CALL_REASONS), 20
            )

    def fill_capacity(self) -> None:
        """Book filler appointments so some days are busy and a few are nearly full."""
        rng, conn, cfg = self.rng, self.conn, self.cfg
        pool = [p for p in self.people if not p.get("tenant")]
        service_types = [NO_HEAT, NO_COOLING, TUNE_UP, SAFETY_CHECK]
        for offset in range(DAYS_OF_CAPACITY):
            day = self.start + timedelta(days=offset)
            for window in cfg.windows:
                if window.after_hours or not cfg.window_offered(day, window):
                    continue
                free = capacity.slots(conn, cfg, day, window, SERVICE_UNIT, [], "Urgent")
                if offset in NEARLY_FULL:
                    keep_open = NEARLY_FULL[offset].get(window.id, 0)
                else:
                    keep_open = int(free.total * (1 - rng.uniform(0.2, 0.6))) - free.held
                keep_open = max(keep_open, 0)
                for _ in range(max(0, free.open - free.held - keep_open)):
                    options = capacity.slots(
                        conn, cfg, day, window, SERVICE_UNIT, [], "Urgent"
                    ).open_ids
                    tech_id = rng.choice(options)
                    skills = set(
                        json.loads(
                            conn.execute(
                                "SELECT skills_json FROM technicians WHERE id = ?", (tech_id,)
                            ).fetchone()[0]
                        )
                    )
                    fits = [t for t in service_types if set(cfg.job_type(t)["skills"]) <= skills]
                    person = rng.choice(pool)
                    wk.add_job(
                        conn,
                        cfg,
                        person["cid"],
                        person["lid"],
                        rng.choice(fits),
                        day,
                        window.id,
                        tech_id=tech_id,
                        summary="Booked by phone.",
                        created=self.stamp(rng.randint(0, 5)),
                    )
        # Comfort advisors get a light schedule of estimates.
        for offset in range(DAYS_OF_CAPACITY):
            day = self.start + timedelta(days=offset)
            for window in cfg.windows[:2]:
                if not cfg.window_offered(day, window):
                    continue
                for tech_id in capacity.slots(
                    conn, cfg, day, window, INSTALL_UNIT, [], "Urgent"
                ).open_ids:
                    if rng.random() < 0.35:
                        person = rng.choice(pool)
                        wk.add_job(
                            conn,
                            cfg,
                            person["cid"],
                            person["lid"],
                            ESTIMATE,
                            day,
                            window.id,
                            tech_id=tech_id,
                            summary="Replacement estimate.",
                            created=self.stamp(rng.randint(0, 5)),
                        )
        self.scenarios["nearlyFullDays"] = {
            (self.start + timedelta(days=o)).isoformat(): v for o, v in NEARLY_FULL.items()
        }


def build(conn: sqlite3.Connection, cfg: TenantConfig, start: date) -> dict:
    world = World(conn, cfg, start)
    world.technicians()
    world.shifts()
    world.named_customers()
    world.random_customers()
    world.past_jobs_and_calls()
    world.fill_capacity()
    return world.scenarios
