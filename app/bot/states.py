from aiogram.fsm.state import State, StatesGroup


class FilterEdit(StatesGroup):
    value = State()  # waiting for the user to type a value; FSM data: filter_id, field
