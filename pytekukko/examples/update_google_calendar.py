#!/usr/bin/env python3

"""Update next collection date events in Google Calendar."""

import asyncio
import datetime
import logging
import sys
from typing import Any, Dict, Mapping, NamedTuple, cast

try:
    import zoneinfo  # type: ignore[import]
except ImportError:  # Python < 3.9
    from backports import zoneinfo  # type: ignore[import]

from google.oauth2 import service_account  # type: ignore
from googleapiclient.discovery import build  # type: ignore

from pytekukko.examples import arg_environ_default, example_argparser, example_client

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


class CalendarData(NamedTuple):
    """Helper container for calendar data to update."""

    name: str
    date: datetime.date
    location: str


# https://developers.google.com/resources/api-libraries/documentation/calendar/v3/python/latest/calendar_v3.events.html
def update_google_calendar(  # pylint: disable=too-many-locals
    credentials: service_account.Credentials,
    calendar_id: str,
    data: Mapping[str, CalendarData],
) -> None:
    """Update the calendar."""
    service = build(
        "calendar",
        "v3",
        credentials=credentials,
        # https://github.com/googleapis/google-api-python-client/issues/299
        # https://github.com/googleapis/google-api-python-client/issues/325
        cache_discovery=False,
    )

    # Events with only date set are apparently treated as occurring at midnight,
    # start of day in calendar's timezone.
    calendar = (
        service.calendars()  # pylint: disable=no-member
        .get(calendarId=calendar_id)
        .execute()
    )
    if calendar.get("timeZone"):
        timezone: datetime.tzinfo = zoneinfo.ZoneInfo(calendar["timeZone"])
    else:
        timezone = datetime.timezone.utc
    since = datetime.datetime.now(timezone).replace(hour=0) - datetime.timedelta(
        hours=1
    )

    events = (
        service.events()  # pylint: disable=no-member
        .list(
            calendarId=calendar_id,
            orderBy="startTime",
            singleEvents=True,
            timeMin=since.isoformat(),
            privateExtendedProperty="pytekukko-managed=true",
        )
        .execute()
    )

    pos_events: Dict[str, Dict[str, Any]] = {}
    for event in events["items"]:
        # Store first event for each pos for update, delete rest
        pos = event["extendedProperties"]["private"].get("pytekukko-pos")
        if pos and pos in data and not pos_events.get(pos):
            pos_events[pos] = event
        else:
            service.events().delete(  # pylint: disable=no-member
                calendarId=calendar_id, eventId=event["id"]
            ).execute()

    for pos, pos_event_data in data.items():
        date = pos_event_data.date.isoformat()
        description = ("{} [pos={}]".format(pos_event_data.name or "", pos)).strip()
        event_data = {
            "summary": "Jätekukko collection",
            "description": description,
            "location": pos_event_data.location or None,
            "start": {"date": date},
            "end": {"date": date},
            # Reminders is "for the authenticated user" per docs.
            # So for the service account, not the calendar owner :(
            # No way to set this for the "actual" calendar user from here,
            # at least while using a service account.
            "reminders": {"useDefault": False},
            "transparency": "transparent",
            "extendedProperties": {
                "private": {"pytekukko-managed": "true", "pytekukko-pos": pos}
            },
            "source": {
                "url": "https://tilasto.jatekukko.fi/indexservice2.jsp",
                "title": "Omakukko",
            },
            "creator": {
                "displayName": "Pytekukko",
            },
        }

        event = pos_events.get(pos)

        method = None
        log_level = logging.INFO
        if event:
            for key, value in event_data.items():
                if event.get(key) != value:
                    action = "updated"
                    method = service.events().patch(  # pylint: disable=no-member
                        calendarId=calendar_id, eventId=event["id"], body=event_data
                    )
                    break
            else:
                action = "unchanged"
                log_level = logging.DEBUG
        else:
            action = "created"
            method = service.events().insert(  # pylint: disable=no-member
                calendarId=calendar_id, body=event_data
            )
        if method:
            event = method.execute()
        LOGGER.log(
            log_level,
            "Event %s: %s",
            action,
            event.get("htmlLink"),  # pytype: disable=attribute-error
        )


async def run_example() -> None:
    """Run the example."""

    argparser = example_argparser(__doc__)
    argparser.add_argument(
        "--google-calendar-id",
        type=str,
        **arg_environ_default("PYTEKUKKO_GOOGLE_CALENDAR_ID"),  # type: ignore[arg-type]
    )
    argparser.add_argument(
        "--google-service-account-file",
        type=str,
        **arg_environ_default(  # type: ignore[arg-type]
            "PYTEKUKKO_GOOGLE_SERVICE_ACCOUNT_FILE", fallback="service_account.json"
        ),
    )
    args = argparser.parse_args()

    if not args.google_calendar_id:
        print("Google calendar id required", file=sys.stderr)
        sys.exit(2)

    client, cookie_jar, cookie_jar_path = example_client(args)

    credentials = service_account.Credentials.from_service_account_file(
        args.google_service_account_file
    )

    async with client.session:
        services = await client.get_services()
        if not cookie_jar_path:
            await client.logout()

    if cookie_jar_path:
        cookie_jar.save(cookie_jar_path)

    data = {}
    for service in (x for x in services if x.next_collection):
        data[str(service.pos)] = CalendarData(
            name=service.name,
            date=cast(datetime.date, service.next_collection),
            location=", ".join(
                x
                for x in (
                    service.raw_data.get("owner", {}).get("katu"),
                    service.raw_data.get("owner", {}).get("posti"),
                )
                if x
            ),
        )

    await asyncio.get_event_loop().run_in_executor(
        None, update_google_calendar, credentials, args.google_calendar_id, data
    )


def main() -> None:
    """Run example in event loop."""
    asyncio.get_event_loop().run_until_complete(run_example())


if __name__ == "__main__":
    main()
