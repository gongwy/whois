package net.ripe.db.whois.api.security;

import jakarta.servlet.http.HttpServletRequest;
import net.ripe.db.whois.common.rpsl.ObjectType;
import net.ripe.db.whois.common.rpsl.RpslObject;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@Component
public class OrganisationAccessPolicy {

    public static final String SERVICE_TOKEN_HEADER = "X-WHOIS-Service-Token";

    private final boolean protectionEnabled;
    private final byte[] serviceToken;

    public OrganisationAccessPolicy(
            @Value("${api.rest.organisation.protection-enabled:true}") final boolean protectionEnabled,
            @Value("${api.rest.organisation.service-token:${WHOIS_SERVICE_TOKEN:}}") final String serviceToken) {
        this.protectionEnabled = protectionEnabled;
        this.serviceToken = serviceToken.getBytes(StandardCharsets.UTF_8);
    }

    public boolean isAuthorised(final HttpServletRequest request) {
        if (!protectionEnabled) {
            return true;
        }

        final String suppliedToken = request.getHeader(SERVICE_TOKEN_HEADER);
        if (serviceToken.length == 0 || suppliedToken == null || suppliedToken.isEmpty()) {
            return false;
        }

        return MessageDigest.isEqual(serviceToken, suppliedToken.getBytes(StandardCharsets.UTF_8));
    }

    public boolean mayRead(final HttpServletRequest request, final ObjectType objectType) {
        return objectType != ObjectType.ORGANISATION || isAuthorised(request);
    }

    public boolean mayRead(final HttpServletRequest request, final RpslObject object) {
        return mayRead(request, object.getType());
    }
}
