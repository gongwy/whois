package net.ripe.db.whois.api.autocomplete;

import jakarta.servlet.http.HttpServletRequest;
import net.ripe.db.whois.api.security.OrganisationAccessPolicy;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anySet;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AutocompleteServiceTest {

    private final AutocompleteSearch autocompleteSearch = mock(AutocompleteSearch.class);
    private final OrganisationAccessPolicy organisationAccessPolicy = mock(OrganisationAccessPolicy.class);
    private final HttpServletRequest request = mock(HttpServletRequest.class);
    private final AutocompleteService subject = new AutocompleteService(autocompleteSearch, organisationAccessPolicy);

    @Test
    void anonymousRequestsExcludeOrganisationResults() throws Exception {
        when(organisationAccessPolicy.isAuthorised(request)).thenReturn(false);
        when(autocompleteSearch.search(any(), anySet(), anySet(), anySet(), eq(false)))
                .thenReturn(List.<Map<String, Object>>of());

        subject.lookup(
                request,
                "ORG-PT",
                "organisation",
                List.of("org-name"),
                Set.of(),
                Set.of(),
                Set.of(),
                null);

        verify(autocompleteSearch).search(any(), anySet(), anySet(), anySet(), eq(false));
    }

    @Test
    void serviceIdentityIncludesOrganisationResults() throws Exception {
        when(organisationAccessPolicy.isAuthorised(request)).thenReturn(true);
        when(autocompleteSearch.search(any(), anySet(), anySet(), anySet(), eq(true)))
                .thenReturn(List.<Map<String, Object>>of());

        subject.lookup(
                request,
                "ORG-PT",
                "organisation",
                List.of("org-name"),
                Set.of(),
                Set.of(),
                Set.of(),
                null);

        verify(autocompleteSearch).search(any(), anySet(), anySet(), anySet(), eq(true));
    }
}
