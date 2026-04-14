-- this could be parsed as well, but we want to avoid that overhead for each integration test
-- all resources listed here are in region
-- the out of region resources should be mentioned in comments to they're easier to find when creating tests
INSERT INTO authoritative_resource (source, resource) VALUES ('cernet', 'AS4538');
INSERT INTO authoritative_resource (source, resource) VALUES ('cernet', 'AS142650');
INSERT INTO authoritative_resource (source, resource) VALUES ('cernet', '::/0');
INSERT INTO authoritative_resource (source, resource) VALUES ('cernet', '0.0.0.0/0');

INSERT INTO transfer_update_lock VALUES (0);
