# TurnB ATS — project instructions

## Azure cost rules (mandatory, every session)

Read and follow these rules before running any command that could touch Azure
— including `python manage.py test`, any `az` command, and any T-SQL against
the Azure SQL server:

@AZURE_COST_RULES.md

## Running tests

Always run tests on SQLite: `DB_ENGINE=sqlite python manage.py test`.
`HR_management/settings.py` also forces SQLite for `test`. Never remove that
guard.
