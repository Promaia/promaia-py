"""Minimal MIDI-style schedule grid for agent scheduling."""

import asyncio
from typing import List, Tuple, Optional, Set
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.application import Application
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from rich.console import Console


# Default time slots (24-hour format)
DEFAULT_TIME_SLOTS = [
    "00:00", "01:00", "02:00", "03:00", "04:00", "05:00",
    "06:00", "07:00", "08:00", "09:00", "10:00", "11:00",
    "12:00", "13:00", "14:00", "15:00", "16:00", "17:00",
    "18:00", "19:00", "20:00", "21:00", "22:00", "23:00",
]

DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def format_schedule_grid(
    time_slots: List[str],
    scheduled: Set[Tuple[int, int]],
    current_row: int,
    current_col: int
) -> str:
    """
    Format the schedule grid as a clean ASCII table.

    Args:
        time_slots: List of time strings (e.g., ["06:00", "09:00", ...])
        scheduled: Set of (row, col) tuples that are scheduled
        current_row: Current cursor row
        current_col: Current cursor column

    Returns:
        Formatted grid string
    """
    lines = []

    # Header row
    header = "Time    " + "  ".join(f"{day:^3}" for day in DAYS_OF_WEEK)
    lines.append(header)

    # Data rows
    for row_idx, time_slot in enumerate(time_slots):
        row_parts = [f"{time_slot}"]

        for col_idx in range(7):
            is_scheduled = (row_idx, col_idx) in scheduled
            is_current = (row_idx == current_row and col_idx == current_col)

            if is_current:
                # Current cell - show with highlight
                cell = " ▶ " if is_scheduled else " > "
            else:
                # Regular cell
                cell = "  ■ " if is_scheduled else "  □ "

            row_parts.append(cell)

        lines.append("".join(row_parts))

    return "\n".join(lines)


def get_status_line(scheduled: Set[Tuple[int, int]]) -> str:
    """Generate status line with stats."""
    total_runs = len(scheduled)

    # Find next run
    if scheduled:
        # Sort by day, then time
        sorted_schedule = sorted(scheduled, key=lambda x: (x[1], x[0]))
        next_day_idx, next_time_idx = sorted_schedule[0]
        next_day = DAYS_OF_WEEK[next_day_idx]
        next_time = DEFAULT_TIME_SLOTS[next_time_idx]
        next_info = f"Next: {next_day} {next_time}"
    else:
        next_info = "No runs scheduled"

    return f"\n{total_runs} runs/week | {next_info}"


def get_instructions() -> str:
    """Get instruction line."""
    return "↑↓←→:Navigate SPACE:Toggle T:Type time C:Clear day ENTER:Confirm ESC:Cancel"


async def select_schedule(
    time_slots: Optional[List[str]] = None,
    preselected: Optional[Set[Tuple[int, int]]] = None
) -> Optional[List[Tuple[str, str]]]:
    """
    Interactive schedule grid selector.

    Args:
        time_slots: Optional list of time slots (defaults to hourly 00:00-23:00)
        preselected: Optional pre-selected schedule as (row, col) tuples

    Returns:
        List of (day, time) tuples like [("Mon", "09:00"), ("Tue", "12:00"), ...]
        or None if cancelled
    """
    console = Console()

    # Use defaults if not provided
    if time_slots is None:
        time_slots = DEFAULT_TIME_SLOTS

    # State
    scheduled: Set[Tuple[int, int]] = set()
    if preselected:
        scheduled = preselected.copy()

    # Custom times: Dict[day_idx, List[time_str]]
    custom_times: Dict[int, List[str]] = {}

    current_row = 0
    current_col = 0
    should_exit = False
    confirmed = False

    def get_display_text():
        """Generate the full display."""
        grid = format_schedule_grid(time_slots, scheduled, current_row, current_col)

        # Add custom times display
        custom_display = ""
        if custom_times:
            custom_display = "\n\nCustom times:"
            for day_idx in sorted(custom_times.keys()):
                day_name = DAYS_OF_WEEK[day_idx]
                times = ", ".join(sorted(custom_times[day_idx]))
                custom_display += f"\n  {day_name}: {times}"

        # Calculate total runs including custom times
        total_grid_runs = len(scheduled)
        total_custom_runs = sum(len(times) for times in custom_times.values())
        total_runs = total_grid_runs + total_custom_runs

        status = f"\n{total_runs} runs/week"
        instructions = get_instructions()

        return f"\n{grid}{custom_display}{status}\n{instructions}\n"

    # Create display windows
    def create_layout():
        display_window = Window(
            FormattedTextControl(text=get_display_text),
            wrap_lines=False,
        )

        container = HSplit([display_window])
        return Layout(container)

    layout = create_layout()

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_row
        if current_row > 0:
            current_row -= 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_row
        if current_row < len(time_slots) - 1:
            current_row += 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Left)
    def move_left(event):
        nonlocal current_col
        if current_col > 0:
            current_col -= 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(Keys.Right)
    def move_right(event):
        nonlocal current_col
        if current_col < 6:
            current_col += 1
            layout = create_layout()
            event.app.layout = layout

    @bindings.add(' ')  # Spacebar
    def toggle_cell(event):
        nonlocal scheduled
        cell = (current_row, current_col)

        if cell in scheduled:
            scheduled.remove(cell)
        else:
            scheduled.add(cell)

        layout = create_layout()
        event.app.layout = layout

    @bindings.add('t')
    @bindings.add('T')
    def type_custom_time(event):
        nonlocal custom_times, should_exit
        # Mark that we want to exit for custom time input
        should_exit = True
        event.app.exit(result='custom_time')

    @bindings.add('c')
    @bindings.add('C')
    def clear_day(event):
        nonlocal scheduled, custom_times

        # Clear all scheduled slots for current day (column)
        scheduled = {(r, c) for r, c in scheduled if c != current_col}

        # Clear custom times for current day
        if current_col in custom_times:
            del custom_times[current_col]

        layout = create_layout()
        event.app.layout = layout

    @bindings.add(Keys.Enter)
    def confirm_selection(event):
        nonlocal should_exit, confirmed
        should_exit = True
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        nonlocal should_exit
        should_exit = True
        event.app.exit()

    # Create application
    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )

    # Run the application in a loop to handle custom time input
    while True:
        result = await app.run_async()

        # Check if user wants to add custom time
        if result == 'custom_time':
            # Get custom time from user
            console.print(f"\nEnter custom time for {DAYS_OF_WEEK[current_col]} (HH:MM format, e.g., 5:56):")
            time_input = input("Time: ").strip()

            if time_input:
                # Validate and add time
                if ':' in time_input:
                    try:
                        hour, minute = time_input.split(':')
                        hour = int(hour)
                        minute = int(minute)

                        if 0 <= hour <= 23 and 0 <= minute <= 59:
                            formatted_time = f"{hour:02d}:{minute:02d}"

                            if current_col not in custom_times:
                                custom_times[current_col] = []

                            if formatted_time not in custom_times[current_col]:
                                custom_times[current_col].append(formatted_time)
                                console.print(f"✓ Added {DAYS_OF_WEEK[current_col]} {formatted_time}", style="green")
                            else:
                                console.print(f"⚠️  Time already exists", style="yellow")
                        else:
                            console.print("❌ Invalid time (hour: 0-23, minute: 0-59)", style="red")
                    except ValueError:
                        console.print("❌ Invalid format. Use HH:MM", style="red")
                else:
                    console.print("❌ Invalid format. Use HH:MM", style="red")

            # Recreate and continue
            should_exit = False
            confirmed = False
            layout = create_layout()
            app = Application(
                layout=layout,
                key_bindings=bindings,
                full_screen=False,
                mouse_support=False,
            )
            continue

        # Normal exit
        break

    if confirmed:
        if not scheduled and not custom_times:
            console.print("❌ No schedule selected", style="red")
            return None

        # Convert (row, col) tuples to (day, time) tuples
        result = []

        # Add grid-based schedule
        for row_idx, col_idx in scheduled:
            day = DAYS_OF_WEEK[col_idx]
            time = time_slots[row_idx]
            result.append((day, time))

        # Add custom times
        for day_idx, times in custom_times.items():
            day = DAYS_OF_WEEK[day_idx]
            for time in times:
                result.append((day, time))

        return result
    else:
        return None


def schedule_to_string(schedule: List[Tuple[str, str]]) -> str:
    """
    Convert schedule to a readable string.

    Args:
        schedule: List of (day, time) tuples

    Returns:
        Human-readable schedule string
    """
    if not schedule:
        return "No schedule"

    # Group by day
    by_day = {}
    for day, time in schedule:
        if day not in by_day:
            by_day[day] = []
        by_day[day].append(time)

    # Sort times within each day
    for day in by_day:
        by_day[day].sort()

    # Format
    parts = []
    for day in DAYS_OF_WEEK:
        if day in by_day:
            times = ", ".join(by_day[day])
            parts.append(f"{day}: {times}")

    return " | ".join(parts)


def schedule_to_cron_expressions(schedule: List[Tuple[str, str]]) -> List[str]:
    """
    Convert schedule to cron expressions.

    Args:
        schedule: List of (day, time) tuples

    Returns:
        List of cron expressions
    """
    # Map day names to cron day numbers (0=Sunday, 1=Monday, ...)
    day_to_cron = {
        "Sun": "0",
        "Mon": "1",
        "Tue": "2",
        "Wed": "3",
        "Thu": "4",
        "Fri": "5",
        "Sat": "6",
    }

    cron_expressions = []

    for day, time in schedule:
        hour, minute = time.split(":")
        day_num = day_to_cron[day]

        # Cron format: minute hour day-of-month month day-of-week
        cron = f"{minute} {hour} * * {day_num}"
        cron_expressions.append(cron)

    return cron_expressions


def schedule_from_interval(interval_minutes: int) -> List[Tuple[str, str]]:
    """
    Convert old interval format to schedule format.
    For backwards compatibility.

    Args:
        interval_minutes: Interval in minutes

    Returns:
        Schedule as list of (day, time) tuples
    """
    # For simple intervals, just schedule every N hours on all days
    if interval_minutes == 60:
        # Every hour, 9-5 on weekdays
        times = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]
        schedule = []
        for day in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
            for time in times:
                schedule.append((day, time))
        return schedule

    elif interval_minutes == 30:
        # Every 30 minutes, 9-5 on weekdays
        times = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
                 "12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
                 "15:00", "15:30", "16:00", "16:30", "17:00"]
        schedule = []
        for day in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
            for time in times:
                schedule.append((day, time))
        return schedule

    # Default: run a few times a day
    return [
        ("Mon", "09:00"), ("Mon", "12:00"), ("Mon", "18:00"),
        ("Tue", "09:00"), ("Tue", "12:00"), ("Tue", "18:00"),
        ("Wed", "09:00"), ("Wed", "12:00"), ("Wed", "18:00"),
        ("Thu", "09:00"), ("Thu", "12:00"), ("Thu", "18:00"),
        ("Fri", "09:00"), ("Fri", "12:00"), ("Fri", "18:00"),
    ]
