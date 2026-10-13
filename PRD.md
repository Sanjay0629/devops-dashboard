# Centralized DevOps Monitoring and Management Portal

**Document Type:** Product Requirements Document (PRD)  
**Version:** 1.0  
**Status:** Initial implementation specification  
**Product Name:** DevOps Control Plane  
**Product Type:** Internal, secure, multi-cloud web application

---

## 1. Product Overview

### 1.1 Purpose

Build a centralized web application that allows DevOps engineers to monitor, troubleshoot, and manage servers and applications distributed across AWS, Microsoft Azure, and other cloud or on-premises environments.

The application will provide a unified interface for infrastructure monitoring, systemd service management, centralized log analysis, alert management, deployment orchestration, and operational auditing.

The goal is to eliminate the need to manually log in to individual servers for routine operational tasks.

### 1.2 Problem Statement

DevOps engineers often manage multiple servers, applications, environments, and cloud providers. Each environment may require a different interface, command, credential, or monitoring tool.

This creates several operational challenges:

- Checking the health of individual servers manually.
- SSHing into servers to inspect systemd services.
- Finding application errors across multiple servers.
- Identifying which servers are experiencing resource pressure.
- Triggering and tracking deployments in separate CI/CD systems.
- Restarting failed services through manual intervention.
- Tracking which engineer performed a production operation.
- Maintaining consistent visibility across AWS, Azure, and other infrastructure.

The proposed application consolidates these workflows into a single, secure operational portal.

### 1.3 Product Vision

Provide one secure interface from which an authorized engineer can answer:

1. Which servers are healthy, degraded, or unavailable?
2. Which systemd services have failed?
3. Which applications are experiencing errors?
4. Which servers are running out of CPU, memory, or disk capacity?
5. Which deployments are running, completed, or failed?
6. What changed on a server before an incident?
7. Can an approved service operation or deployment be initiated safely?
8. Who performed an administrative action, and what was its outcome?

---

## 2. Product Goals

### Primary goals

- Centralize server inventory across multiple cloud providers.
- Display infrastructure health and resource utilization.
- Monitor systemd services and application health.
- Centralize application and system logs.
- Provide configurable alerts and notifications.
- Trigger and monitor CI/CD deployments.
- Provide controlled service start, stop, and restart operations.
- Implement authentication, authorization, approvals, and audit logging.
- Support incremental onboarding of servers and applications.
- Provide a consistent interface regardless of the underlying cloud provider.

### Secondary goals

- Support environment-level views for development, staging, and production.
- Maintain deployment and operational history.
- Reduce repetitive manual troubleshooting.
- Support additional cloud providers through extensible adapters.
- Provide operational reports and historical trends.

### Success metrics

The product should be evaluated using the following measurable outcomes:

- Percentage of registered servers reporting health data.
- Time taken to identify a failed service.
- Time taken to locate relevant application errors.
- Percentage of deployments with complete execution history.
- Percentage of privileged operations recorded in the audit log.
- Alert delivery success rate.
- Number of manual server logins needed for routine monitoring.

Initial targets should be established after a baseline measurement. The application must not report a server as healthy merely because no alert has been received.

---

## 3. Target Users and Roles

### 3.1 DevOps Engineer

Responsibilities:
- Monitor servers and applications.
- Inspect systemd services and logs.
- Acknowledge alerts.
- Trigger authorized deployments.
- Execute permitted service operations.
- Investigate incidents.

### 3.2 Administrator

Responsibilities:
- Manage users and roles.
- Configure cloud integrations.
- Register servers and applications.
- Configure monitoring targets and alert rules.
- Manage environment permissions.
- Review audit events.

### 3.3 Developer

Responsibilities:
- View authorized application health.
- Inspect application logs permitted by their role.
- View deployment history.
- Trigger approved non-production deployments if authorized.

### 3.4 Read-Only User

Responsibilities:
- View authorized dashboards, servers, metrics, alerts, and deployment history.
- Cannot modify configuration or execute operations.

### 3.5 Approver

Responsibilities:
- Review production deployment requests.
- Approve or reject high-impact operations.
- Review the associated request details and execution history.

### 3.6 Authorization rules

- Every user must authenticate before accessing protected resources.
- Every backend endpoint must enforce authorization.
- Permissions must be scoped by environment, application, server, and action where applicable.
- UI visibility must not be treated as an authorization boundary.
- Production permissions must be explicitly granted.
- High-impact operations must support an approval policy.
- Users must not approve their own requests when separation of duties is required.

---

## 4. Functional Requirements

## 4.1 Authentication and User Management

**Priority: P0 — Mandatory**

Requirements:

- Support secure login through OpenID Connect (OIDC).
- Support a configurable identity provider.
- Enforce session expiration and secure logout.
- Support role-based access control (RBAC).
- Allow administrators to assign roles and environment permissions.
- Restrict access to administrative functions.
- Record successful and failed authentication events.
- Protect API endpoints against unauthorized access.
- Never store plaintext passwords.

Acceptance criteria:

- An unauthenticated user cannot access protected pages or APIs.
- A read-only user cannot trigger deployments or service operations.
- An unauthorized production action is rejected by the backend.
- Revoked permissions take effect without requiring a new application release.

## 4.2 Global Dashboard

**Priority: P0 — Mandatory**

The dashboard must display:

- Total registered servers.
- Healthy, warning, critical, and unreachable servers.
- Servers with stale monitoring data.
- CPU utilization.
- Memory utilization.
- Disk utilization.
- Network activity where available.
- Failed systemd services.
- Active alerts.
- Recent deployments.
- Recent administrative operations.
- Recent incidents and health changes.

Filters:

- Cloud provider.
- Environment.
- Region.
- Application.
- Server.
- Operating system.
- Health status.
- Team or owner.

Requirements:

- Display the last successful data collection time.
- Distinguish between a healthy server and a server that has stopped reporting.
- Provide links from summary cards to the relevant detailed view.
- Refresh data automatically at a configurable interval.
- Display loading, empty, stale-data, and error states.

Acceptance criteria:

- Selecting a dashboard status filter updates the displayed server list.
- A server that has stopped reporting is marked stale or unreachable according to configured thresholds.
- The dashboard must not silently replace unavailable data with zero values.

## 4.3 Server Inventory

**Priority: P0 — Mandatory**

The application must maintain a centralized inventory of managed servers.

Server attributes:

- Server ID.
- Display name.
- Hostname.
- Cloud provider.
- Provider resource ID.
- Cloud account or subscription reference.
- Region.
- Environment.
- Operating system and version.
- Private IP address, where available.
- Application associations.
- Owner or responsible team.
- Management method.
- Monitoring-agent status.
- Last-seen timestamp.
- Registration timestamp.
- Operational status.

Supported initial environments:

- AWS EC2.
- Azure Virtual Machines.
- Registered Linux servers outside those environments.
- Other cloud VMs through an extensible adapter model.

Features:

- Register a server.
- Edit server metadata.
- View server details.
- Filter and search inventory.
- Enable or disable monitoring.
- Associate servers with applications.
- Record onboarding and connectivity status.
- Prevent duplicate registration of the same provider resource.

Server registration must not automatically grant unrestricted administrative permissions.

Acceptance criteria:

- An administrator can register a server with the required metadata.
- Duplicate provider resource identifiers are detected within their relevant account or subscription.
- The inventory clearly distinguishes managed, pending, stale, and unavailable servers.

## 4.4 Infrastructure Monitoring

**Priority: P0 — Mandatory**

Technology:

- Prometheus for time-series metrics.
- Node Exporter for Linux host metrics.
- Appropriate Windows exporters for Windows hosts.
- Systemd Exporter or a restricted agent integration for systemd-specific metrics.
- Provider-specific integrations for cloud metrics when necessary.

Metrics to display:

- CPU utilization.
- Memory utilization.
- Available memory.
- Disk usage and available disk space.
- Disk I/O.
- Network throughput.
- System load average.
- Server uptime.
- Monitoring-agent health.
- Metric collection timestamp.

Features:

- Current metric cards.
- Historical time-series charts.
- Configurable time ranges.
- Server comparison.
- Environment comparison.
- Threshold indicators.
- Links to relevant alerts.

Acceptance criteria:

- Metrics are associated with the correct server.
- The UI shows the time range and timestamp of the displayed data.
- Missing data is represented as unavailable rather than zero.
- Historical metrics are available according to the configured retention policy.

## 4.5 systemd Service Monitoring

**Priority: P0 — Mandatory**

The application must display the state of selected systemd services on Linux servers.

Example services:

- nginx.service
- php8.3-fpm.service
- redis-server.service
- docker.service
- Custom application API services.
- Worker and scheduler services.

Service attributes:

- Service name.
- Server ID.
- Load state.
- Active state.
- Substate.
- Enabled or disabled state, where available.
- Main process ID, where available.
- Restart count, where available.
- Last state change, where available.
- Last successful collection timestamp.

Required service states include:

- Active.
- Inactive.
- Failed.
- Activating.
- Deactivating.
- Unknown or unavailable.

Features:

- List services across all servers.
- Filter failed services.
- Search by service name.
- View service details.
- View recent service logs.
- View service state history where collection is supported.
- Link service failures to alerts and server details.

Acceptance criteria:

- A failed service is identifiable without logging in to its server.
- The service state is accompanied by its collection timestamp.
- An unreachable server is not incorrectly presented as having all services inactive.
- The system distinguishes unsupported, uncollected, and failed states.

## 4.6 Service Management

**Priority: P1 — Required after monitoring foundations**

Supported initial operations:

- Start an approved service.
- Stop an approved service.
- Restart an approved service.
- Retrieve service status.
- Retrieve a bounded amount of recent service logs.

Features:

- Display a confirmation dialog before executing a state-changing action.
- Show the target server, service, environment, and requested operation.
- Enforce a configurable service allowlist.
- Enforce action-specific permissions.
- Require approval for designated production operations.
- Display execution progress and final status.
- Record command or provider-operation metadata without exposing secrets.
- Verify the resulting service state.
- Apply operation timeouts and rate limits.

Execution states:

- Pending.
- Awaiting approval.
- Approved.
- Running.
- Succeeded.
- Failed.
- Timed out.
- Cancelled.
- Rejected.

Security requirements:

- Never execute arbitrary commands supplied by the frontend.
- Never expose a general-purpose root shell through the API.
- Do not trust service names or server IDs without validation.
- Use provider-native management APIs or a narrowly scoped execution agent.
- Ensure the execution identity has only the required permissions.

Acceptance criteria:

- A user without the required permission cannot start, stop, or restart a service.
- An approved restart creates an operation record.
- The UI shows the execution result and verified service state.
- Failed operations preserve diagnostic output subject to redaction and access controls.
- Duplicate requests do not accidentally execute the same operation multiple times.

## 4.7 Centralized Log Management

**Priority: P0 — Mandatory**

Technology:

- Grafana Alloy for log collection.
- Grafana Loki for log storage and querying.

Log sources:

- Linux system logs.
- systemd journal entries.
- Nginx access and error logs.
- PHP-FPM logs.
- FastAPI and other application logs.
- Docker container logs.
- Deployment logs, through the CI/CD integration.

Features:

- Search logs across authorized servers.
- Filter by application, server, environment, service, severity, and time range.
- Search for common error patterns.
- Display timestamps and source information.
- Navigate from a failed service to related logs.
- Navigate from a deployment to its associated logs.
- Support bounded log queries and pagination.
- Configure retention and storage limits.
- Enforce access controls for sensitive logs.

Security requirements:

- Redact secrets and credentials where feasible before ingestion.
- Avoid using unbounded, high-cardinality labels.
- Restrict access to production logs.
- Prevent the portal from exposing unrestricted filesystem access.

Acceptance criteria:

- An authorized user can search logs from multiple registered servers.
- Log results show their source and timestamp.
- Unauthorized users cannot query restricted environments.
- Queries respect configured limits and retention.

## 4.8 Alert Management and Notifications

**Priority: P0 — Mandatory**

Initial alert conditions:

- Server stops reporting.
- CPU utilization remains above a configured threshold.
- Memory pressure remains above a configured threshold.
- Disk utilization exceeds a configured threshold.
- A monitored systemd service enters the failed state.
- An application health check fails.
- A deployment fails.
- An administrative operation times out or fails.

Features:

- Configurable warning and critical thresholds.
- Alert severity and status.
- Alert creation and resolution timestamps.
- Acknowledgement and assignment.
- Alert history.
- Notification routing.
- Deduplication and grouping.
- Links to affected servers, services, and applications.

Possible notification channels:

- Email.
- Microsoft Teams.
- Slack.
- Other supported webhook destinations.

Acceptance criteria:

- A configured alert condition produces an alert event.
- Repeated occurrences are grouped or deduplicated according to policy.
- Resolved alerts retain their history.
- Notification failures are observable and retryable.
- Alert acknowledgement does not incorrectly mark the underlying problem as resolved.

## 4.9 Application Management

**Priority: P1 — Required**

The application must support registering logical applications independently of individual servers.

Application attributes:

- Application ID.
- Application name.
- Description.
- Owning team.
- Environment.
- Associated servers.
- Associated systemd services.
- Repository reference.
- CI/CD pipeline reference.
- Health-check endpoint reference.
- Deployment configuration reference.

Features:

- View application health.
- View associated infrastructure.
- View associated services.
- View application logs.
- View deployment history.
- View recent alerts.
- View current deployed version or commit when available.

Acceptance criteria:

- One application can be associated with multiple servers.
- One server can host multiple applications.
- The UI clearly displays application health separately from host health.

## 4.10 Deployment Management

**Priority: P1 — Required**

Initial integration:

- Jenkins REST API and pipeline integration.

The portal must orchestrate existing CI/CD pipelines rather than recreate the build system.

Features:

- Register an application and its pipeline.
- Select an application.
- Select an authorized environment.
- Select an approved branch, release, or commit.
- Display the exact deployment target.
- Require approval according to environment policy.
- Trigger the pipeline.
- Display build and deployment progress.
- Display pipeline logs where authorized.
- Record commit SHA, pipeline ID, requester, approver, timestamps, and result.
- Run configured post-deployment health checks.
- Display the final deployment outcome.
- Support a defined rollback workflow when the deployment system provides one.

Deployment states:

- Requested.
- Awaiting approval.
- Approved.
- Queued.
- Running.
- Succeeded.
- Failed.
- Cancelled.
- Rolled back.

Security requirements:

- Restrict selectable pipelines and deployment targets to registered configurations.
- Never accept arbitrary Jenkins URLs or arbitrary build parameters from untrusted users.
- Store integration credentials in a secrets manager.
- Use separate permissions for staging and production.
- Require approval for production according to policy.
- Prevent duplicate deployment requests through idempotency controls.
- Never report deployment success until the pipeline and configured health checks satisfy the success policy.

Acceptance criteria:

- An authorized user can trigger a registered non-production pipeline.
- Pipeline progress and result are visible in the portal.
- Production deployments follow the configured approval workflow.
- Every deployment has a complete audit record.
- Failed deployments display useful diagnostic information.

## 4.11 Cloud Integrations

**Priority: P1 — Required**

### AWS

Initial integration:

- AWS Systems Manager for supported server operations.
- IAM roles and least-privilege policies.
- CloudWatch metrics where useful.
- AWS resource inventory where required.

Requirements:

- Prefer role-based authentication.
- Avoid static access keys.
- Restrict operations to authorized accounts, regions, resources, and actions.
- Handle API throttling and permission errors.

### Microsoft Azure

Initial integration:

- Azure management APIs.
- Azure VM Run Command where appropriate.
- Managed identities or another approved identity mechanism.
- Azure Monitor integration where required.
- Azure Arc for supported external server-management scenarios.

Requirements:

- Apply least-privilege role assignments.
- Restrict resource-group and subscription scope.
- Respect provider-specific execution limitations.
- Clearly report unavailable or unsupported operations.

### Other infrastructure

Support registered Linux servers through an approved restricted agent or management gateway.

New providers must implement a documented adapter interface rather than introducing provider-specific logic throughout the frontend.

Acceptance criteria:

- Provider-specific failures produce understandable error messages.
- One provider's outage does not make the entire portal unusable.
- Unsupported operations are explicitly identified.

## 4.12 Audit and Operational History

**Priority: P0 — Mandatory**

Record:

- User authentication events.
- Server registration and configuration changes.
- Role and permission changes.
- Service operations.
- Deployment requests and execution results.
- Approval decisions.
- Cloud integration changes.
- Relevant failed authorization attempts.

Each event should contain:

- Event ID.
- Actor or service identity.
- Action type.
- Target resource.
- Environment.
- Request timestamp.
- Correlation or operation ID.
- Approval reference, where applicable.
- Outcome.
- Sanitized diagnostic metadata.

Requirements:

- Protect audit records against unauthorized modification and deletion.
- Restrict audit access to authorized users.
- Avoid recording plaintext credentials, access tokens, or sensitive request bodies.
- Configure retention and export policies.

Acceptance criteria:

- Every privileged operation can be traced to its initiating identity.
- Operation history includes the result and associated execution ID.
- Users cannot alter audit records through ordinary application APIs.

---

## 5. Technical Architecture

### 5.1 Frontend

Technology:

- React.
- TypeScript.
- Vite.
- Tailwind CSS.
- A consistent reusable component system.
- A charting library suitable for time-series data.

Responsibilities:

- Display dashboards, tables, charts, logs, and alerts.
- Provide search, filtering, sorting, and pagination.
- Handle authentication state.
- Display asynchronous operation progress.
- Show confirmation and approval dialogs.
- Provide accessible error, loading, and empty states.

The frontend must not contain cloud credentials, provider secrets, privileged commands, or authorization logic that is relied upon for security.

### 5.2 Backend

Technology:

- Python.
- FastAPI.
- Pydantic for request and response validation.
- SQLAlchemy or an equivalent supported ORM.
- Alembic for database migrations.

Responsibilities:

- Authentication and authorization enforcement.
- Server and application inventory.
- Provider adapters.
- Metrics and log-query APIs.
- Service-operation orchestration.
- Deployment orchestration.
- Approval workflows.
- Audit recording.
- API validation and rate limiting.

Use a modular monolith for the initial release. Separate modules and worker processes by responsibility, but do not introduce unnecessary microservices.

### 5.3 Background processing

Technology:

- Celery.
- Redis as the initial task broker.

Use asynchronous jobs for:

- Service operations.
- Cloud API requests that may take time.
- Deployment tracking.
- Health checks.
- Notifications.
- Scheduled inventory synchronization.

Long-running jobs must not block ordinary API requests.

### 5.4 Data storage

PostgreSQL stores application metadata and operational state.

Prometheus stores infrastructure time-series metrics.

Loki stores collected logs.

Object storage may be added for exported reports or approved artifacts.

Redis stores transient job and queue state; it must not be the only durable record of a privileged operation.

### 5.5 Provider adapter interface

Define a common interface for provider-specific operations.

Required logical operations:

- Get server information.
- Check management connectivity.
- Get operation status.
- Retrieve approved service information.
- Execute an approved operation.
- Return structured execution results.

Each adapter must handle:

- Authentication errors.
- Authorization errors.
- Timeouts.
- Provider API throttling.
- Unsupported operations.
- Partial failures.
- Retry policies.

### 5.6 Monitoring and logging integrations

- Prometheus collects metrics.
- Node Exporter provides Linux host metrics.
- A systemd exporter or restricted agent provides selected service information.
- Windows exporters provide appropriate Windows metrics.
- Grafana Alloy collects supported logs and telemetry.
- Loki stores and queries logs.
- Alerting rules generate actionable alert events.

The portal should integrate with these systems through documented APIs and service interfaces.

---

## 6. Database Model

Create normalized PostgreSQL tables with appropriate indexes, foreign keys, timestamps, and migration support.

Initial entities:

### users
- id
- identity_provider_subject
- email
- display_name
- status
- created_at
- updated_at

### roles
- id
- name
- description

### permissions
- id
- resource_type
- action
- scope_definition

### user_roles
- user_id
- role_id
- scope_definition

### cloud_accounts
- id
- provider
- account_reference
- display_name
- region_configuration
- status
- created_at

### servers
- id
- cloud_account_id
- provider_resource_id
- hostname
- display_name
- operating_system
- environment
- region
- management_method
- status
- last_seen_at
- created_at
- updated_at

### applications
- id
- name
- description
- owner_team
- repository_reference
- status
- created_at

### application_servers
- application_id
- server_id

### services
- id
- server_id
- service_name
- monitoring_enabled
- action_policy_id
- last_observed_state
- last_observed_at

### deployments
- id
- application_id
- environment
- pipeline_reference
- commit_sha
- requested_by
- approval_status
- execution_status
- external_execution_id
- started_at
- completed_at
- created_at

### operations
- id
- target_server_id
- target_service_id
- action_type
- requested_by
- approval_status
- execution_status
- idempotency_key
- external_execution_id
- started_at
- completed_at
- sanitized_result

### approvals
- id
- operation_type
- operation_id
- requested_by
- approved_by
- decision
- reason
- created_at
- decided_at

### audit_events
- id
- actor_id
- action_type
- target_type
- target_id
- correlation_id
- outcome
- sanitized_metadata
- created_at

### alert_events
- id
- source
- target_type
- target_id
- severity
- status
- summary
- first_seen_at
- last_seen_at
- acknowledged_by
- resolved_at

Additional tables may be introduced when justified by concrete requirements.

Do not duplicate raw Prometheus metrics or entire log streams in PostgreSQL.

---

## 7. API Requirements

Use versioned REST endpoints under `/api/v1`.

Representative endpoints:

### Dashboard
- GET /api/v1/dashboard/summary
- GET /api/v1/dashboard/health

### Servers
- GET /api/v1/servers
- POST /api/v1/servers
- GET /api/v1/servers/{server_id}
- PATCH /api/v1/servers/{server_id}
- GET /api/v1/servers/{server_id}/metrics
- GET /api/v1/servers/{server_id}/services

### Services
- GET /api/v1/servers/{server_id}/services/{service_name}
- GET /api/v1/servers/{server_id}/services/{service_name}/logs
- POST /api/v1/servers/{server_id}/services/{service_name}/operations
- GET /api/v1/operations/{operation_id}

### Applications
- GET /api/v1/applications
- POST /api/v1/applications
- GET /api/v1/applications/{application_id}
- GET /api/v1/applications/{application_id}/health

### Logs
- POST /api/v1/logs/query

### Alerts
- GET /api/v1/alerts
- GET /api/v1/alerts/{alert_id}
- POST /api/v1/alerts/{alert_id}/acknowledge

### Deployments
- GET /api/v1/deployments
- POST /api/v1/deployments
- GET /api/v1/deployments/{deployment_id}
- POST /api/v1/deployments/{deployment_id}/approve
- POST /api/v1/deployments/{deployment_id}/reject

### Audit
- GET /api/v1/audit-events

API rules:

- Validate all request bodies and path parameters.
- Enforce authorization on every resource.
- Use pagination for collection endpoints.
- Apply query and execution time limits.
- Return structured error responses.
- Support correlation IDs.
- Use idempotency keys for state-changing requests where appropriate.
- Never expose provider credentials or raw secret values.
- Do not expose arbitrary shell-execution endpoints.

These endpoints are an initial contract; refine request and response schemas during implementation.

---

## 8. User Interface Requirements

Required navigation:

1. Dashboard
2. Servers
3. Applications
4. Services
5. Logs
6. Alerts
7. Deployments
8. Operations History
9. Audit Logs
10. Integrations
11. Users and Roles
12. Settings

### Dashboard page

Display:

- Health summary cards.
- Resource utilization charts.
- Failed services.
- Active alerts.
- Recent deployments.
- Recent operations.
- Server status table.

### Server details page

Tabs:

- Overview.
- Metrics.
- Services.
- Logs.
- Applications.
- Operations history.

### Service details page

Display:

- Current state.
- Last-seen timestamp.
- Recent logs.
- Related alerts.
- Approved actions.
- Operation history.

### Deployment page

Display:

- Application.
- Environment.
- Source branch or commit.
- Requester.
- Approval status.
- Pipeline status.
- Build logs.
- Health-check results.
- Rollback availability.

### UI design principles

- Use a consistent professional operations-dashboard design.
- Support dark and light themes.
- Prioritize information density without sacrificing readability.
- Clearly distinguish healthy, warning, critical, stale, and unknown states.
- Require confirmation before privileged operations.
- Show the exact target and environment before execution.
- Display asynchronous progress and final outcomes.
- Never use color as the only indicator of status.
- Support desktop layouts first, with responsive behavior for smaller screens.

---

## 9. Security and Compliance Requirements

Security is a mandatory release requirement, not a later enhancement.

### Identity and authorization

- Use OIDC authentication.
- Enforce RBAC and resource-level scope.
- Validate authorization in backend services.
- Apply least-privilege cloud IAM roles and Azure role assignments.
- Separate production and non-production permissions.

### Network security

- Use HTTPS for external access.
- Keep internal services on private networks where possible.
- Restrict exporter and management ports to approved sources.
- Prefer outbound agent connections or provider-native management channels.
- Do not require public SSH access to every managed server.

### Secrets management

- Use AWS Secrets Manager, Azure Key Vault, or an approved secret store.
- Never commit secrets to source control.
- Never return secrets through frontend APIs.
- Rotate integration credentials according to policy.
- Redact secrets from application, operation, and deployment logs.

### Administrative execution

- Support fixed, allowlisted operations only.
- Validate server identity, service name, operation type, and user permissions.
- Require approvals for configured high-risk actions.
- Apply timeouts and rate limits.
- Record requests and outcomes.
- Protect against duplicate submissions.
- Use a restricted execution identity rather than unrestricted root access.

### Audit protection

- Record privileged actions and relevant authorization failures.
- Restrict audit access.
- Prevent ordinary users from editing or deleting audit events.
- Export important audit records to an independently protected destination where required.
- Configure retention policies.

### Application security

- Validate and sanitize input.
- Use parameterized database queries.
- Protect against CSRF where cookie-based authentication requires it.
- Apply secure cookie settings.
- Configure CORS narrowly.
- Implement rate limiting.
- Protect against SSRF, particularly in log-query, health-check, webhook, and integration configuration.
- Avoid unsafe URL fetching from user-controlled input.
- Run dependency and container image scans.
- Use secure defaults for error handling.

---

## 10. Non-Functional Requirements

### Reliability

- A failure in one cloud provider integration must not take down the entire portal.
- Provider API failures must be surfaced as errors or stale data.
- Long-running tasks must have explicit timeouts.
- Operations must have traceable lifecycle states.
- Retries must avoid unintended duplicate actions.

### Performance

Initial targets, to be validated through testing:

- Dashboard API responses: p95 under 2 seconds for ordinary cached or indexed queries.
- Server inventory queries: p95 under 2 seconds for the initial expected workload.
- Interactive navigation: usable without full-page reloads.
- Log queries: bounded by time range, result limit, and execution timeout.
- Background operations: report progress independently of request-response latency.

These targets are initial engineering goals, not measured guarantees.

### Scalability

- Support multiple cloud providers and accounts.
- Separate metric and log storage from application metadata.
- Support additional worker instances.
- Use pagination and bounded queries.
- Support configurable metric and log retention.
- Avoid requiring a full application redesign when additional servers are onboarded.

### Availability and recovery

- Monitor the health of the portal itself.
- Back up PostgreSQL.
- Document recovery procedures.
- Keep durable records of privileged operations.
- Define recovery objectives before production rollout.
- Make monitoring and log collection failures visible.

### Maintainability

- Use typed frontend code.
- Use type-checked backend request and response models.
- Maintain automated tests.
- Use version-controlled database migrations.
- Document environment configuration.
- Separate configuration from application code.
- Pin and regularly update dependencies.
- Use structured logging and correlation IDs.

---

## 11. Deployment and Development Environment

### Local development

Use Docker Compose for local development.

Expected services:

- Frontend.
- FastAPI backend.
- Background worker.
- PostgreSQL.
- Redis.
- Prometheus.
- Grafana.
- Loki.
- Grafana Alloy where required for local testing.

External cloud integrations must be optional during local development. The application should start and operate in a demonstration mode without production cloud credentials.

### Production deployment

Initially deploy the application in a dedicated management environment.

Requirements:

- HTTPS entry point.
- Secure authentication.
- Private networking for internal components.
- Persistent database storage.
- Persistent metric and log storage.
- Backups.
- Secrets-manager integration.
- Monitoring for the portal itself.
- Resource limits.
- Centralized application logs.
- Documented upgrade and rollback procedures.

Use Docker Compose for the initial proof of concept if suitable. Reassess high availability and orchestration requirements before production expansion.

### Environment configuration

Provide an `.env.example` containing placeholders only.

Configuration categories:

- Application environment.
- API base URL.
- PostgreSQL connection settings.
- Redis connection settings.
- Prometheus endpoint.
- Loki endpoint.
- OIDC issuer and client configuration.
- Secret-store configuration.
- Provider integration settings.
- Jenkins integration settings.
- Log and metric retention settings.

Never include real credentials in example files.

---

## 12. Testing Requirements

### Unit tests

Test:

- Permission evaluation.
- Server state classification.
- Service-state normalization.
- Alert condition evaluation.
- Deployment state transitions.
- Operation state transitions.
- Provider adapter error handling.
- Secret redaction.
- Input validation.

### Integration tests

Test:

- FastAPI and PostgreSQL interactions.
- Database migrations.
- Redis and worker processing.
- Prometheus queries.
- Loki queries.
- Cloud adapter authentication and errors.
- Jenkins integration using a test pipeline.
- Approval workflows.
- Audit event creation.

### End-to-end tests

Test:

- Login and logout.
- Dashboard navigation.
- Server registration.
- Server health inspection.
- Service status inspection.
- Log search.
- Alert acknowledgement.
- Non-production deployment triggering.
- Unauthorized action rejection.
- Production approval enforcement.

### Failure scenarios

Test:

- Server becomes unreachable.
- Monitoring data becomes stale.
- Exporter stops reporting.
- systemd service fails.
- Cloud provider API times out.
- Jenkins is unavailable.
- Log storage is unavailable.
- Worker crashes during an operation.
- Duplicate requests arrive.
- User permissions are revoked during a session.

### Release criteria

A release is not complete until:

- Critical authorization tests pass.
- Privileged actions are audited.
- Secret leakage tests pass.
- Failed and timed-out operations are represented correctly.
- Database migrations are tested.
- Deployment and recovery procedures are documented.

---

## 13. Phased Delivery Plan

### Phase 1 — Foundation

Deliver:
- Project structure.
- Authentication.
- RBAC.
- PostgreSQL schema.
- Server inventory.
- Application inventory.
- Docker Compose development environment.
- Initial dashboard.

### Phase 2 — Monitoring

Deliver:
- Prometheus integration.
- Linux host metrics.
- systemd service monitoring.
- Windows metrics where required.
- Historical charts.
- Server health and stale-data detection.

### Phase 3 — Logs and alerts

Deliver:
- Alloy and Loki integration.
- Centralized log search.
- Alert rules.
- Alert acknowledgement.
- Notification integration.

### Phase 4 — Controlled service operations

Deliver:
- Provider adapters.
- Restricted service actions.
- Approval policies.
- Background operation tracking.
- Operation history.
- Audit trail.

### Phase 5 — Deployment management

Deliver:
- Jenkins integration.
- Registered pipeline configuration.
- Deployment history.
- Build and deployment logs.
- Production approval workflows.
- Post-deployment health checks.

### Phase 6 — Hardening and expansion

Deliver:
- Additional provider integrations.
- Expanded role and scope controls.
- Security testing.
- Backup and recovery verification.
- Performance testing.
- Production readiness documentation.

Prioritize a functioning, secure vertical slice over implementing every module simultaneously.

---

## 14. Out of Scope for the Initial Release

The following features are not mandatory for the first release:

- Replacing Jenkins with a custom CI/CD engine.
- Building a custom metrics database.
- Building a custom log-storage engine.
- A general-purpose remote terminal.
- Arbitrary root command execution.
- Automated remediation of every possible incident.
- Automatic production deployments without approval policies.
- A full Kubernetes management platform.
- Complex multi-region high availability before requirements justify it.
- AI-driven autonomous infrastructure changes.

These capabilities may be considered later through explicit requirements and risk assessment.

---

## 15. Final Definition of Done

The initial product is considered functionally complete when an authorized DevOps engineer can:

1. Sign in securely.
2. View registered AWS, Azure, and other supported servers.
3. Inspect server resource utilization.
4. Identify failed or stale systemd services.
5. Search authorized logs across servers.
6. View and acknowledge alerts.
7. Trigger an approved non-production deployment through Jenkins.
8. Track the progress and outcome of the deployment.
9. Execute an authorized, allowlisted service operation.
10. Review the complete history of privileged operations.
11. Confirm that unauthorized operations are blocked.
12. Verify that secrets are not exposed through the UI, APIs, or logs.
13. Recover application metadata using the documented backup procedure.

**The first release must prioritize reliable monitoring, secure access control, service visibility, and auditability before enabling broad production management capabilities.**