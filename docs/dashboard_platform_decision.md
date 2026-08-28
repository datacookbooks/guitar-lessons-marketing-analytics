# Dashboard Platform and Connectivity Decision

Status: Complete
Date: 2026-08-26
Completed: 2026-08-28

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
* The project will use one standardized semantic model. Physical separation of
  a thin report from that model remains subject to Publish-to-web compatibility
  testing during the next milestone.
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
3. The BI login cannot read staging tables or modify persistent database objects
   or source data.
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

Power BI Desktop runs in Windows through Parallels on the project owner's Mac.
Import mode is the selected storage choice because the current data volume is
modest and near-real-time reporting is not required.

The proposed path is:

1. Power BI Desktop runs in the Windows virtual machine.
2. It connects to RDS from the currently approved public IPv4 `/32`.
3. It authenticates with a dedicated read-only BI login.
4. It imports only approved analytics dimensions and reporting views.
5. One standardized semantic model will own relationships, the date table,
   shared DAX measures, formats, descriptions, and hidden technical fields.
6. The report will use that canonical model without duplicating business logic;
   a physically separate thin-report configuration will be used only if its
   Publish-to-web compatibility is proven.
7. The semantic model and report are published to the Power BI service.
8. Public portfolio sharing uses a manually refreshed static Import snapshot
   without weakening RDS network access.

Import mode is selected because:

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
* no permission to insert, update, delete, or truncate source data, or to create,
  alter, or drop persistent objects in the approved analytical schemas;
* PostgreSQL's effective `TEMPORARY` database privilege follows the database-wide
  `PUBLIC` policy rather than a role-specific denial;
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

Power BI Desktop running in Parallels reached RDS through the Mac's approved
public IP. The first encrypted connection failed because Windows did not trust
the RDS certificate chain. Installing the official Amazon RDS `us-east-1` CA
bundle in the current Windows user's trusted-root store resolved the issue
without bypassing certificate validation or encryption.

The RDS security group must not be broadened to `0.0.0.0/0` or `::/0`.

The selected design uses manual Import refresh in Power BI Desktop followed by
republication. The Power BI service receives the imported snapshot and does
not require a direct RDS connection. The project will not configure a gateway,
service-side database credentials, DirectQuery, or broader RDS ingress merely
to enable cloud refresh.

## Publication design

The owner created an independently controlled Microsoft Entra ID Free tenant
rather than relying on a school-controlled identity. A dedicated cloud-only
Power BI identity has the `Fabric Administrator` role and a `Fabric (Free)`
license. No Power BI Pro, Premium Per User, Fabric capacity trial, Azure paid
capacity, or Azure workload was purchased or activated.

The tenant's Publish-to-web setting is enabled only for the assigned-membership
security group `Power BI Publish to Web Creators`. The dedicated Power BI
identity is a direct member. New embed-code creation was explicitly enabled;
the tenant-wide Block Public Internet Access setting remains disabled because
anonymous publication requires public reachability.

The disposable `guitar-analytics-connectivity-test` report was published from
Power BI Desktop to `My workspace`. The service created both a report and its
Import semantic model. The Fabric Free identity successfully created a
Publish-to-web code without a Pro purchase. The public URL loaded without
authentication in an incognito browser, and the generated report worked
normally inside a local HTML iframe. Filters and report interactions remained
functional. The public-view options were inspected and showed no unexpected
or sensitive fields.

Publish to web makes the report and the data included in its semantic model
intentionally public. Permanent publication is therefore limited to synthetic
portfolio data. Credentials, RDS endpoints, public IPs, AWS identifiers, and
unnecessary customer-level fields must remain outside public artifacts.

The disposable embed code, report, and semantic model are being retained
temporarily as a known-working publication reference. They must be removed when
the final portfolio report replaces them. This retention is not a licensing
requirement: separate reports can have separate embed codes.

## Refresh design

Power BI Desktop successfully refreshed the disposable Import model from RDS
over verified TLS. Before relocation, the remaining approved BI objects were
imported into the canonical working PBIX, and Model view confirmed that every
loaded table uses Import storage mode. The completed import is a point-in-time
copy stored in the PBIX; reopening the file does not automatically contact RDS.

The final public report will use a reviewed static Import snapshot. Intentional
updates will use a manual Desktop refresh from an authorized public IPv4 `/32`,
followed by republication. No Power BI service manual or scheduled database
refresh is required. No gateway, service-side RDS credentials, DirectQuery, or
continuous RDS availability will be configured for the public portfolio.

Away from an authorized network, the owner can safely build relationships, a
date table, DAX, formats, report pages, visuals, slicers, and navigation from
the saved Import model. Source refresh, adding PostgreSQL objects, and
source-dependent Power Query changes must wait for an authorized network.

## Completed hands-on tests

1. Power BI Desktop `2.157.879.0`, 64-bit (August 2026), opened and operated
   correctly through Parallels.
2. The guarded database-access runner created and verified the reusable
   `marketing_analytics_bi_reader` `NOLOGIN` role against the confirmed
   `guitar_analytics` database.
3. A separate operational login inherited the role, defaulted to read-only
   transactions, read the approved objects, and was denied staging access and
   persistent writes.
4. Representative approved queries returned one reporting-cutoff row, 53,567
   monthly-movement rows, and 32 campaign-incremental rows.
5. Power BI Desktop connected to PostgreSQL from Windows through the existing
   `/32`, preserved verified TLS, imported usable PostgreSQL types, rendered a
   minimal report, and completed a manual refresh.
6. The remaining approved objects were imported into the canonical working
   PBIX before relocation, and every loaded table was verified as Import mode.
7. The dedicated Fabric Free identity accessed `My workspace` and published
   the disposable report and semantic model.
8. The restricted Publish-to-web tenant setting allowed that identity to
   create a new embed code without Pro or PPU.
9. The public URL rendered anonymously in an incognito browser, and the report
   remained interactive inside a local iframe.
10. Public-view options were inspected; only approved synthetic test content
    was observed.
11. Service-side database refresh was deliberately not configured. The static
    Import design avoids a gateway, scheduled refresh, service credentials,
    DirectQuery, and broadened RDS access.
12. The disposable public artifacts are intentionally retained only until the
    final portfolio report replaces them, at which point their embed code,
    report, and semantic model must be deleted.

## Spike deliverables

The completed spike produced:

* this platform and connectivity decision document;
* versioned least-privilege PostgreSQL access SQL;
* automated structural tests for that SQL where appropriate;
* read-only connectivity evidence;
* a minimal disposable Power BI artifact;
* publication and refresh findings;
* an updated `ARCHITECTURE.md`;
* a documented static Import refresh decision; and
* exact instructions for the following semantic-model milestone.

## Final result

The spike is complete. Power BI is viable for this Mac-based public portfolio:
Desktop authoring works through Parallels, RDS access remains restricted to one
public IPv4 `/32`, the dedicated login is least-privilege, verified TLS and
manual Desktop refresh work, and the approved data is stored in Import mode.

An independently controlled Fabric Free tenant can publish the disposable
Import report from `My workspace`, create a Publish-to-web code, serve the
report anonymously, and render it interactively in an iframe. A Power BI Pro
purchase is not required for the tested path. The final design therefore uses
a manually refreshed and republished static snapshot with no gateway,
scheduled service refresh, DirectQuery, service-side RDS credentials, paid
Fabric capacity, or broadened database ingress.

The known-working disposable publication remains active temporarily for
reference and must be removed when the final report replaces it. The next
milestone will build and reconcile the canonical semantic model and report
from the already imported sources.

## Exact next semantic-model work

The semantic-model milestone is now the next task. It will use the approved
objects already stored in the canonical Import PBIX and will not require an
immediate RDS refresh.

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

The eventual report will remain logically thin: it will not duplicate stable
business rules already owned by PostgreSQL or the canonical Power BI model.
Before physically separating the report from the semantic model, test that the
exact configuration remains compatible with Publish to web. Keeping the model
and report together in one canonical PBIX remains acceptable if needed for the
public-publication path.

PostgreSQL will continue to own source cleaning, deterministic row selection,
eligibility, attribution, cohort, churn, experiment, CLV, and data-quality
rules. Power BI will own filter-context-dependent aggregation, presentation,
and interaction.
