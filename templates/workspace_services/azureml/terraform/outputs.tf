output "azureml_workspace_name" {
  value = azapi_resource.aml_workspace.output.name
}

output "azureml_acr_id" {
  value = azurerm_container_registry.acr.id
}

output "azureml_storage_account_id" {
  value = azurerm_storage_account.aml.id
}

output "aml_fqdn" {
  value = regex("(?:(?P<scheme>[^:/?#]+):)?(?://(?P<fqdn>[^/?#:]*))?", module.terraform_azurerm_environment_configuration.aml_studio_endpoint).fqdn
}

output "connection_uri" {
  value = format("%s/?wsid=%s&tid=%s", module.terraform_azurerm_environment_configuration.aml_studio_endpoint, azapi_resource.aml_workspace.output.id, var.arm_tenant_id)
}

output "workspace_address_spaces" {
  value = data.azurerm_virtual_network.ws.address_space
}

output "aml_subnet_address_prefixes" {
  value = azurerm_subnet.aml.address_prefixes
}

# The service tags API uses short region codes for some regions, for example switzerlandn for switzerlandnorth,
# so azurerm_network_service_tags cannot find their regional tags. A regional tag name is the service name and
# the region display name without spaces, for example Storage.SwitzerlandNorth.
data "azurerm_location" "aml" {
  location = azurerm_storage_account.aml.location
}

locals {
  service_tag_region = replace(data.azurerm_location.aml.display_name, " ", "")
}

output "storage_tag" {
  value = "Storage.${local.service_tag_region}"
}

output "mcr_tag" {
  value = "MicrosoftContainerRegistry.${local.service_tag_region}"
}

output "batch_tag" {
  value = "BatchNodeManagement.${local.service_tag_region}"
}
