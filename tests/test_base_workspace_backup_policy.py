import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from jsonschema.exceptions import ValidationError

from db.repositories.resource_templates import ResourceTemplateRepository
from db.repositories.resources import ResourceRepository
from models.domain.resource import ResourceType
from models.schemas.resource import ResourcePatch
from models.schemas.resource_template import ResourceTemplateInCreate
from resources.strings import RESOURCE_ACTION_UPDATE


BUNDLE = Path(__file__).resolve().parents[1] / "templates/workspaces/base"
SCHEMA = json.loads((BUNDLE / "template_schema.json").read_text(encoding="utf-8"))
POLICIES = ("vm_backup_policy", "fileshare_backup_policy")


@pytest.mark.asyncio
async def test_registered_template_accepts_creation_and_updates():
    template_repo = ResourceTemplateRepository()
    template_repo.save_item = AsyncMock()
    template = await template_repo.create_template(
        ResourceTemplateInCreate(name="base", version="2.13.0", current=True, json_schema=SCHEMA),
        ResourceType.Workspace,
    )
    template = type(template).model_validate(template.model_dump())
    enriched = template_repo.enrich_template(template)
    properties = {
        "display_name": "Backup test",
        "description": "Backup test",
        "auth_type": "Automatic",
        "address_space_size": "small",
        "enable_backup": True,
        "vm_backup_policy": {"time": "21:30", "daily_count": 30, "weekly_weekdays": ["Saturday", "Sunday"]},
        "fileshare_backup_policy": {"time": "23:30", "daily_count": 21, "yearly_count": 0},
    }

    ResourceRepository._validate_resource_parameters({"properties": properties}, enriched)
    ResourceRepository._validate_resource_parameters(
        {"properties": {key: value for key, value in properties.items() if key not in POLICIES}}, enriched
    )
    repository = ResourceRepository()
    for policy in POLICIES:
        repository.validate_patch(
            ResourcePatch(properties={policy: properties[policy]}), template_repo, template, RESOURCE_ACTION_UPDATE
        )

    invalid = [
        ("vm_backup_policy", {"time": "25:00"}),
        ("vm_backup_policy", {"time": "22:15"}),
        ("vm_backup_policy", {"daily_count": 6}),
        ("fileshare_backup_policy", {"daily_count": 201}),
        ("fileshare_backup_policy", {"weekly_count": -1}),
        ("fileshare_backup_policy", {"monthly_count": 121}),
        ("fileshare_backup_policy", {"yearly_count": 11}),
        ("vm_backup_policy", {"weekly_weekdays": []}),
        ("vm_backup_policy", {"yearly_weeks": ["Fifth"]}),
        ("vm_backup_policy", {"yearly_months": ["December", "December"]}),
        ("vm_backup_policy", {"unexpected": True}),
    ]
    for policy, value in invalid:
        with pytest.raises(ValidationError):
            repository.validate_patch(
                ResourcePatch(properties={policy: value}), template_repo, template, RESOURCE_ACTION_UPDATE
            )


def test_porter_defaults_and_lifecycle_pass_policy_inputs():
    porter = yaml.safe_load((BUNDLE / "porter.yaml").read_text(encoding="utf-8"))
    parameters = {parameter["name"]: parameter for parameter in porter["parameters"]}
    for policy in POLICIES:
        assert json.loads(base64.b64decode(parameters[policy]["default"])) == {}
        for action in ("install", "upgrade", "uninstall"):
            terraform_step = next(step["terraform"] for step in porter[action] if "terraform" in step)
            assert terraform_step["vars"][policy] == f"${{ bundle.parameters.{policy} }}"


@pytest.mark.parametrize(("policy", "time"), [("vm_backup_policy", "22:00"), ("fileshare_backup_policy", "23:00")])
def test_form_defaults_match_original_terraform_policy(policy, time):
    expected = {
        "timezone": "UTC",
        "time": time,
        "daily_count": 14,
        "weekly_count": 4,
        "weekly_weekdays": ["Sunday"],
        "monthly_count": 12,
        "monthly_weekdays": ["Monday"],
        "monthly_weeks": ["First"],
        "yearly_count": 2,
        "yearly_weekdays": ["Sunday"],
        "yearly_weeks": ["Last"],
        "yearly_months": ["December"],
    }
    fields = SCHEMA["properties"][policy]["properties"]
    assert {name: field["default"] for name, field in fields.items()} == expected
