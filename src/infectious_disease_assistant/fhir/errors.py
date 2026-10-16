class FhirError(RuntimeError):
    """The synthetic FHIR adapter could not complete a retrieval."""


class FhirTransportError(FhirError):
    pass


class FhirResponseError(FhirError):
    pass


class FhirResourceNotFoundError(FhirResponseError):
    pass


class FhirParseError(FhirError):
    pass
