# Azure Health Data Services Workspace Service

See [Azure Health Data Services Documentation](https://learn.microsoft.com/en-us/azure/healthcare-apis/healthcare-apis-overview).

## Prerequisites

- [A base workspace deployed](https://microsoft.github.io/AzureTRE/tre-templates/workspaces/base/)

## Azure Healthcare Workspace

Each Azure Health Data Services workspace service creates a [Healthcare Workspace](https://learn.microsoft.com/en-us/azure/healthcare-apis/workspace-overview).
In addition, when creating this workspace service you can choose to deploy [FHIR](https://learn.microsoft.com/en-us/azure/healthcare-apis/fhir/) and [DICOM](https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/) instances within the newly created healthcare workspace.

![Healthcare Service](images/hs_details.png)

## Private network access

The service connects the healthcare workspace to the workspace network through one private endpoint.
Azure writes the private DNS records for FHIR and DICOM when it creates the DNS zone group of this endpoint.
Azure does not always add a record for a service that an update adds later.
The name of the DNS zone group shows the services, for example `private-dns-zone-group-fhir-dicom`.
If an update adds or removes FHIR or DICOM, Terraform creates the DNS zone group again, and Azure writes the records again.
The endpoint stays. While Terraform creates the group again, the private DNS records do not exist.

Before version 0.3.13, the DNS zone group did not change when an update added a service.
If you added FHIR or DICOM to an existing service before this version, the new host can resolve to a public address.
An upgrade to version 0.3.13 gives the DNS zone group a new name, so Azure writes the missing record.
Do not delete the private endpoint to repair the service.

The DICOM service has public network access turned off.
Before version 0.3.10, Terraform created the DICOM service with public network access on.
An upgrade to version 0.3.10 turns it off.

The private endpoint on the healthcare workspace blocks public traffic to FHIR and DICOM.
A request from outside the workspace network gets HTTP 403.
The FHIR service reports `publicNetworkAccess` `Enabled`, but Azure does not use this value when the workspace has an approved private endpoint.
Azure also keeps this value when a request sets it to `Disabled`.

## Authentication

Learn more about authentication and application roles in [this doc](https://learn.microsoft.com/en-us/azure/healthcare-apis/authentication-authorization). Make sure to assign your users/apps with the required role and follow the guidelines to retrieve a token.

Notice: If you are using a separate tenant for authentication follow [this documentation](https://learn.microsoft.com/en-us/azure/healthcare-apis/azure-api-for-fhir/configure-local-rbac) to assign users/apps to your FHIR instance.
