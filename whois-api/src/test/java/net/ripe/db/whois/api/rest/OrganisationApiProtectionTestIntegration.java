package net.ripe.db.whois.api.rest;

import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import net.ripe.db.whois.api.AbstractIntegrationTest;
import net.ripe.db.whois.api.RestTest;
import net.ripe.db.whois.api.rest.domain.WhoisResources;
import net.ripe.db.whois.api.security.OrganisationAccessPolicy;
import net.ripe.db.whois.common.dao.jdbc.DatabaseHelper;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import static org.hamcrest.MatcherAssert.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.not;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertEquals;

@Tag("IntegrationTest")
class OrganisationApiProtectionTestIntegration extends AbstractIntegrationTest {

    @BeforeAll
    static void setUpProtection() {
        System.setProperty("api.rest.organisation.protection-enabled", "true");
        System.setProperty("api.rest.organisation.service-token", "test-service-token");
        DatabaseHelper.addGrsDatabases("TEST-GRS");
    }

    @AfterAll
    static void clearProtection() {
        System.clearProperty("api.rest.organisation.protection-enabled");
        System.clearProperty("api.rest.organisation.service-token");
    }

    @BeforeEach
    void addObjects() {
        databaseHelper.addObject(
                "organisation: ORG-PT1-TEST\n" +
                "org-name:     Protected Test Organisation\n" +
                "notify:       security-test@example.net\n" +
                "source:       CERNET");
        databaseHelper.addObject(
                "person:       Protected Test Person\n" +
                "address:      Test Address\n" +
                "phone:        +31 20 000 0000\n" +
                "nic-hdl:      PTP1-TEST\n" +
                "notify:       security-test@example.net\n" +
                "source:       CERNET");
        databaseHelper.addObject(
                "inetnum:      10.200.0.0 - 10.200.0.255\n" +
                "netname:      PROTECTED-TEST\n" +
                "org:          ORG-PT1-TEST\n" +
                "source:       CERNET");
    }

    @Test
    void directOrganisationLookupRequiresServiceIdentity() {
        try (Response anonymousResponse = RestTest.target(
                        getPort(), "whois/CERNET/organisation/ORG-PT1-TEST")
                .request(MediaType.APPLICATION_JSON_TYPE)
                .get()) {
            assertEquals(Response.Status.NOT_FOUND.getStatusCode(), anonymousResponse.getStatus(),
                    () -> anonymousResponse.readEntity(String.class));
        }

        final WhoisResources result = RestTest.target(
                        getPort(), "whois/CERNET/organisation/ORG-PT1-TEST")
                .request(MediaType.APPLICATION_JSON_TYPE)
                .header(OrganisationAccessPolicy.SERVICE_TOKEN_HEADER, "test-service-token")
                .get(WhoisResources.class);

        assertThat(result.getWhoisObjects(), hasSize(1));
    }

    @Test
    void genericSearchHidesOnlyOrganisationWithoutServiceIdentity() {
        final String anonymousResult;
        try (Response anonymousResponse = RestTest.target(
                        getPort(), "whois/search?source=CERNET&query-string=security-test%40example.net&inverse-attribute=notify")
                .request(MediaType.APPLICATION_JSON_TYPE)
                .get()) {
            assertEquals(Response.Status.OK.getStatusCode(), anonymousResponse.getStatus(),
                    () -> anonymousResponse.readEntity(String.class));
            anonymousResult = anonymousResponse.readEntity(String.class);
        }

        assertThat(anonymousResult, containsString("PTP1-TEST"));
        assertThat(anonymousResult, not(containsString("ORG-PT1-TEST")));

        final String serviceResult = RestTest.target(
                        getPort(), "whois/search?source=CERNET&query-string=security-test%40example.net&inverse-attribute=notify")
                .request(MediaType.APPLICATION_JSON_TYPE)
                .header(OrganisationAccessPolicy.SERVICE_TOKEN_HEADER, "test-service-token")
                .get(String.class);

        assertThat(serviceResult, containsString("PTP1-TEST"));
        assertThat(serviceResult, containsString("ORG-PT1-TEST"));
    }

    @Test
    void resourceHolderAnnotationDoesNotRevealOrganisationWithoutServiceIdentity() {
        final String path = "whois/CERNET/inetnum/10.200.0.0%20-%2010.200.0.255?resource-holder";

        final WhoisResources anonymousResult = RestTest.target(getPort(), path)
                .request(MediaType.APPLICATION_JSON_TYPE)
                .get(WhoisResources.class);
        assertNull(anonymousResult.getWhoisObjects().get(0).getResourceHolder());

        final WhoisResources serviceResult = RestTest.target(getPort(), path)
                .request(MediaType.APPLICATION_JSON_TYPE)
                .header(OrganisationAccessPolicy.SERVICE_TOKEN_HEADER, "test-service-token")
                .get(WhoisResources.class);
        assertNotNull(serviceResult.getWhoisObjects().get(0).getResourceHolder());
    }
}
