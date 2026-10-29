package net.ripe.db.whois.api.security;

import jakarta.servlet.http.HttpServletRequest;
import net.ripe.db.whois.common.rpsl.ObjectType;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class OrganisationAccessPolicyTest {

    private final HttpServletRequest request = mock(HttpServletRequest.class);

    @Test
    void disabledProtectionAllowsOrganisationReads() {
        final OrganisationAccessPolicy policy = new OrganisationAccessPolicy(false, "");

        assertTrue(policy.mayRead(request, ObjectType.ORGANISATION));
    }

    @Test
    void enabledProtectionFailsClosedWhenConfiguredTokenIsEmpty() {
        when(request.getHeader(OrganisationAccessPolicy.SERVICE_TOKEN_HEADER)).thenReturn("");
        final OrganisationAccessPolicy policy = new OrganisationAccessPolicy(true, "");

        assertFalse(policy.mayRead(request, ObjectType.ORGANISATION));
    }

    @Test
    void enabledProtectionRejectsMissingAndIncorrectTokens() {
        final OrganisationAccessPolicy policy = new OrganisationAccessPolicy(true, "expected-token");

        assertFalse(policy.mayRead(request, ObjectType.ORGANISATION));
        when(request.getHeader(OrganisationAccessPolicy.SERVICE_TOKEN_HEADER)).thenReturn("wrong-token");
        assertFalse(policy.mayRead(request, ObjectType.ORGANISATION));
    }

    @Test
    void enabledProtectionAllowsExactTokenAndNeverBlocksOtherObjectTypes() {
        when(request.getHeader(OrganisationAccessPolicy.SERVICE_TOKEN_HEADER)).thenReturn("expected-token");
        final OrganisationAccessPolicy policy = new OrganisationAccessPolicy(true, "expected-token");

        assertTrue(policy.mayRead(request, ObjectType.ORGANISATION));
        assertTrue(policy.mayRead(request, ObjectType.PERSON));
        assertTrue(policy.mayRead(request, ObjectType.DOMAIN));
        assertTrue(policy.mayRead(request, ObjectType.INETNUM));
    }
}
