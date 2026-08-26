# Dashboard Platform and Connectivity Decision

Status: Spike in progress
Date: 2026-08-26

## Purpose

This spike selects the dashboard platform and proves its secure connection,
publication, and refresh path before the full semantic model or dashboard is
built.

## Project constraints

* Development occurs on a Mac.
* Power BI Desktop will run in Windows through Parallels on the Mac.
* PostgreSQL runs on Amazon RDS.
* RDS currently accepts PostgreSQL traffic only from the owner's current
  public IPv4 address as a single `/32`.
* The RDS security group must never be broadened to `0.0.0.0/0` or `::/0`.
* The eventual BI connection must use a dedicated least-privilege login.
* The BI login must not receive access to staging tables.
* The portfolio data is synthetic and suitable for intentional public
  publication.
* The current data volume is modest, and near-real-time reporting is not
  required.
* The project will use one standardized semantic model and a thin report.
* Database credentials, endpoints, AWS identifiers, security-group
  identifiers, and public IP addresses must not be committed or shown in
  screenshots.
* The connectivity spike will use only read-only database queries.
* Building the complete semantic model and dashboard is outside this spike.

## Acceptance criteria

The selected Power BI path must demonstrate all of the following:

1. Power BI Desktop authoring works reliably in Windows through Parallels.
2. A dedicated BI login can query only approved analytics dimensions and
   reporting views.
3. The BI login cannot read staging tables or modify any database objects or
   data.
4. The connection preserves the current single-IPv4 `/32` RDS restriction.
5. Representative reporting views return usable fields and data types.
6. The connectivity test causes zero database writes.
7. A minimal disposable semantic model and report can be published to the
   Power BI service.
8. The spike identifies where imported data is stored.
9. The spike identifies where and how database credentials are retained.
10. The spike determines whether scheduled refresh requires an always-on
    gateway.
11. The spike determines whether public portfolio sharing is available with
    the current license and tenant settings.
12. The spike records exactly what becomes publicly accessible through
    Publish to web.
13. The chosen publication and refresh approach has acceptable account and
    cost requirements.
14. The final report can remain thin and use shared, documented metric
    definitions.

## Platforms reviewed

### Power BI

Relevant official documentation:

* https://learn.microsoft.com/en-us/power-bi/transform-model/service-edit-data-models
* https://learn.microsoft.com/en-us/power-query/connectors/postgresql
* https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-install
* https://learn.microsoft.com/en-us/power-bi/connect-data/service-gateway-onprem
* https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-publish-to-web
* https://learn.microsoft.com/en-us/power-bi/connect-data/desktop-report-lifecycle-datasets

Current findings:

* Power BI Desktop is a Windows application, but the project owner can run it
  through Parallels on the Mac.
* The Power BI service supports browser-based editing of Import semantic
  models, including relationships, DAX measures, calculated columns,
  calculated tables, formatting, and row-level security.
* Power BI provides a native PostgreSQL connector.
* The PostgreSQL connector supports cloud connections, virtual-network data
  gateways, and on-premises data gateways.
* Power BI's standard on-premises gateway requires a supported Windows
  computer.
* Microsoft recommends that a gateway run on a computer that remains powered
  on and connected rather than a laptop or virtual machine that is frequently
  asleep, suspended, or offline.
* A gateway running inside Parallels would therefore require the Windows
  virtual machine and Mac to be available whenever a scheduled refresh runs.
* Direct cloud access from the Power BI service to RDS must not be enabled by
  opening PostgreSQL broadly to the internet.
* Publish to web depends on the user's Power BI license, workspace,
  permissions, tenant settings, and administrator configuration.
* Publish to web makes the report publicly accessible without viewer
  authentication.
* Public publication may expose underlying data contained in the semantic
  model and is therefore appropriate only because this project uses synthetic
  portfolio data.
* The project previously lacked Power BI Pro and did not produce a verified
  public Power BI link.
* A prior Power BI project also encountered a `GatewayUnknownError` during a
  semantic-model refresh. The present spike must test the current account and
  PostgreSQL workflow rather than assume that refresh will work.

### Tableau Public

Relevant official documentation:

* https://www.tableau.com/products/techspecs
* https://help.tableau.com/current/pro/desktop/en-us/public_faq.htm
* https://help.tableau.com/current/pro/desktop/en-us/publish_workbooks_tableaupublic.htm
* https://help.tableau.com/current/pro/desktop/en-us/examples_postgresql.htm
* https://help.tableau.com/current/pro/desktop/en-us/desktop_comparison.htm

Current findings:

* Tableau Desktop Public Edition supports Mac and Apple Silicon.
* Tableau Desktop provides a PostgreSQL connector.
* Publishing to Tableau Public creates an extract instead of retaining a live
  PostgreSQL connection.
* Published Tableau Public workbooks and their included data are public and
  may be downloadable.
* Tableau Public does not provide automatic refresh for PostgreSQL extracts.
  Its documented automatic 24-hour refresh applies to Google Sheets.
* Tableau Public would provide a simpler native-Mac public-portfolio path, but
  it is not the selected platform because the project owner has chosen Power
  BI and can run Power BI Desktop through Parallels.

## Platform decision

Power BI is the selected BI platform.

Power BI Desktop will run in Windows through Parallels on the project owner's
Mac. Import mode is the provisional storage choice because the current data
volume is modest and near-real-time reporting is not required.

The proposed path is:

1. Power BI Desktop runs in the Windows virtual machine.
2. It connects to RDS from the currently approved public IPv4 `/32`.
3. It authenticates with a dedicated read-only BI login.
4. It imports only approved analytics dimensions and reporting views.
5. One standardized semantic model owns relationships, the date table, shared
   DAX measures, formats, descriptions, and hidden technical fields.
6. A thin report connects to that semantic model.
7. The semantic model and report are published to the Power BI service.
8. Public portfolio sharing and refresh behavior are tested without weakening
   RDS network access.

Import mode is preferred provisionally because:

* the model is small enough to import efficiently;
* near-real-time reporting is not required;
* report interactions will not depend on continuous RDS availability;
* report visuals will not send interactive queries back to RDS;
* imported data provides predictable portfolio-demo performance; and
* the public report will contain only approved synthetic data.

DirectQuery is not currently justified because it would make RDS availability,
secure network reachability, database performance, and query latency part of
every report interaction.

Composite mode is not justified because the project does not currently need
mixed Import and DirectQuery storage.

## Secure database-access design

Power BI must not connect with the PostgreSQL administrative account used for
pipeline deployment.

A dedicated BI login will be created with these properties:

* permission to connect to the project database;
* `USAGE` only on the approved `analytics` and `reporting` schemas;
* `SELECT` only on specifically approved analytics dimensions and reporting
  views;
* no access to the `staging` schema;
* no access to ETL watermarks or loaded-object manifests;
* no permission to insert, update, delete, truncate, create, alter, or drop
  database objects;
* no superuser, database-creation, role-creation, replication, or row-level
  security bypass privileges; and
* a password stored outside Git and excluded from screenshots and project
  documentation.

The connectivity test will use a read-only transaction and representative
queries against:

* `analytics.vw_reporting_cutoff`;
* `reporting.vw_monthly_paid_movement`; and
* `reporting.vw_campaign_incremental_performance`.

The test must also prove that the BI login cannot query a staging table or
perform a database write.

## Network design

The current RDS security group permits inbound PostgreSQL traffic only from
the project owner's current public IPv4 address as a `/32`.

Power BI Desktop running in Parallels should reach RDS through the Mac's current
internet connection and approved public IP. This must be tested directly.

The RDS security group must not be broadened to `0.0.0.0/0` or `::/0`.

If the Power BI service cannot connect directly to RDS under the existing
restriction, the project will use either:

* a securely configured on-premises gateway running through the Windows
  environment; or
* manual Import refresh in Power BI Desktop followed by republication.

The project will not weaken RDS network access merely to enable cloud refresh.

## Publication design

The spike will publish only a minimal disposable report containing synthetic
data.

Before permanent publication, the spike must verify:

* whether the current Power BI account can publish from My workspace;
* whether Publish to web is enabled for the tenant;
* whether the current license can create a public embed code;
* whether an administrator action is required;
* whether viewers can access the report without authentication;
* whether underlying or summarized data can be downloaded;
* whether the semantic model contains any unnecessary customer-level fields;
* whether database credentials are included in or exposed by the publication;
  and
* how the public artifact can be disabled or deleted.

Publish to web will not be used with real, confidential, personal, or
credential-bearing data.

## Refresh design

Three refresh behaviors will be distinguished during testing:

1. **Power BI Desktop refresh**

   Power BI Desktop connects from Parallels to RDS, refreshes the local Import
   model, and republishes the semantic model and report.

2. **Power BI service manual refresh**

   The Power BI service refreshes the published semantic model using its
   configured connection or gateway.

3. **Power BI service scheduled refresh**

   The Power BI service refreshes automatically on a schedule using a supported
   connection or an available gateway.

A scheduled refresh through an on-premises gateway may require the Parallels
Windows virtual machine and Mac to remain running. That operational dependency
may be excessive for this portfolio.

Manual Desktop refresh and republication may therefore be the preferred
initial approach. This is acceptable because the project does not require
near-real-time reporting and downstream pipeline scheduling remains deferred.

The final refresh decision will be based on the hands-on test rather than this
provisional assessment.

## Planned hands-on tests

1. Record the installed Power BI Desktop version.
2. Confirm that Power BI Desktop opens and operates correctly through
   Parallels.
3. Confirm Power BI service sign-in and record the current license type.
4. Confirm whether My workspace is available.
5. Define and apply versioned least-privilege PostgreSQL access.
6. Verify that the BI login cannot access `staging`.
7. Verify that the BI login cannot modify database data or objects.
8. Query:

   * `analytics.vw_reporting_cutoff`;
   * `reporting.vw_monthly_paid_movement`; and
   * `reporting.vw_campaign_incremental_performance`.
9. Confirm usable Power BI data types and acceptable query behavior.
10. Confirm that the database session and connectivity test cause zero writes.
11. Connect Power BI Desktop to PostgreSQL from Windows in Parallels.
12. Build a minimal Import-mode semantic model containing only approved data.
13. Publish one minimal disposable report to the Power BI service.
14. Test the account's Publish to web availability and document any license,
    tenant, or administrator restriction.
15. Test Power BI Desktop refresh.
16. Test Power BI service manual refresh if a supported secure connection is
    available.
17. Determine whether scheduled refresh requires an always-on gateway.
18. Inspect public access, underlying-data exposure, stored credentials, and
    refresh settings.
19. Delete the disposable report, semantic model, embed code, connection, and
    test credentials unless they are intentionally retained by the final
    design.

## Spike deliverables

The completed spike will produce:

* this platform and connectivity decision document;
* versioned least-privilege PostgreSQL access SQL;
* automated structural tests for that SQL where appropriate;
* read-only connectivity evidence;
* a minimal disposable Power BI artifact;
* publication and refresh findings;
* an updated `ARCHITECTURE.md`;
* a documented final refresh decision; and
* exact instructions for the following semantic-model milestone.

## Final result

To be completed after the hands-on tests.

The final result will record:

* the tested Power BI Desktop and Power BI service versions or account state;
* the selected connection path;
* the verified database privileges;
* the selected storage mode;
* the publication result;
* the refresh result;
* any license or tenant limitation;
* costs and operational dependencies;
* rejected alternatives;
* removed disposable resources; and
* unresolved limitations.

## Exact next semantic-model work

The semantic-model milestone will begin only after this spike proves a
supported secure path.

The planned semantic model will contain:

* one canonical set of approved tables and reporting views;
* explicit one-to-many relationships;
* a dedicated date table;
* documented shared DAX measures;
* numerator-and-denominator measures for non-additive rates;
* friendly business names;
* consistent formats and descriptions;
* hidden technical keys and lineage fields;
* no duplicated report-page business logic; and
* reconciliation measures and queries that match reviewed PostgreSQL results.

The eventual report will remain thin. PostgreSQL will continue to own source
cleaning, deterministic row selection, eligibility, attribution, cohort,
churn, experiment, CLV, and data-quality rules. Power BI will own
filter-context-dependent aggregation, presentation, and interaction.

