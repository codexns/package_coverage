CREATE TABLE coverage_results (
    id integer PRIMARY KEY AUTOINCREMENT,
    project varchar NOT NULL,
    commit_hash varchar NOT NULL,
    commit_summary varchar NOT NULL,
    commit_date timestamp NOT NULL,
    data blob NOT NULL,
    platform varchar NOT NULL,
    python_version varchar NOT NULL,
    path_prefix varchar NOT NULL,
    output varchar NOT NULL
);
