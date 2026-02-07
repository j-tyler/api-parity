# S3 API Specification Reference

Complete S3 API specification reference for bucket operations, object operations,
multipart upload, and list operations. Derived from the official AWS S3 API Reference
and MinIO source code analysis. Contains enough detail to implement or test an
S3-compatible server.

**Scope:** Core data-plane operations only. No IAM, no CloudTrail, no analytics,
no website hosting. Authentication (SigV4) is documented at the request-routing
level but not in cryptographic detail.

**Conventions:**

- All XML bodies use namespace `xmlns="http://s3.amazonaws.com/doc/2006-03-01/"`
- ETags are always quoted strings: `"d41d8cd98f00b204e9800998ecf8427e"`
- Timestamps in XML use ISO 8601: `2009-10-12T17:50:30.000Z`
- Timestamps in HTTP headers use RFC 7231: `Thu, 01 Dec 2009 16:00:00 GMT`
- `{bucket}` and `{key}` are path placeholders
- Headers marked "Echo" are returned with the same value provided in the request

---

## Table of Contents

1. [Common Elements](#1-common-elements)
2. [Bucket Operations](#2-bucket-operations)
3. [Object Operations](#3-object-operations)
4. [Multipart Upload Operations](#4-multipart-upload-operations)
5. [List Operations](#5-list-operations)
6. [Implementation Reference](#6-implementation-reference)
7. [MinIO Deviations from AWS S3](#7-minio-deviations-from-aws-s3)

---

## 1. Common Elements

### 1.1 XML Namespace

All S3 XML request and response bodies use:

```
xmlns="http://s3.amazonaws.com/doc/2006-03-01/"
```

Error responses do NOT include the xmlns attribute. This is consistent across
AWS S3 and MinIO.

### 1.2 Request Routing

**Virtual-hosted style (recommended, default):**

```
https://{bucket}.s3.{region}.amazonaws.com/{key}
```

Bucket extracted from Host header subdomain. Key is the full request URI path.

**Path style (deprecated but still functional):**

```
https://s3.{region}.amazonaws.com/{bucket}/{key}
```

Bucket is first path segment. Key is everything after.

**Host header routing logic for S3-compatible servers:**

1. Host is `s3.{region}.{domain}` — Path-style. Bucket = first path segment,
   key = remainder of path.
2. Host ends with `.s3.{region}.{domain}` — Virtual-hosted. Bucket = subdomain
   prefix before `.s3.`, key = full URI path.
3. Host is anything else (CNAME alias) — Bucket = lowercase Host header value,
   key = full URI path.

**Sub-resource query parameters for operation identification:**

| Query Parameter | Operation |
|-----------------|-----------|
| `?acl` | Get/Put ACL |
| `?cors` | CORS configuration |
| `?delete` | Multi-object delete (POST) |
| `?encryption` | Default encryption |
| `?lifecycle` | Lifecycle configuration |
| `?location` | Bucket location |
| `?logging` | Bucket logging |
| `?notification` | Notification configuration |
| `?object-lock` | Object Lock configuration |
| `?policy` | Bucket policy |
| `?replication` | Replication configuration |
| `?retention` | Object retention |
| `?legal-hold` | Object legal hold |
| `?tagging` | Tags |
| `?uploads` | List multipart uploads (GET on bucket) / Initiate upload (POST on object) |
| `?uploadId={id}` | Complete (POST), abort (DELETE), or list parts (GET) |
| `?partNumber={n}` | Upload part (PUT) |
| `?versioning` | Bucket versioning |
| `?versions` | List object versions |
| `?list-type=2` | ListObjectsV2 |

### 1.3 Common Request Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes (except anonymous) | AWS Signature V4: `AWS4-HMAC-SHA256 Credential=AKID/date/region/s3/aws4_request, SignedHeaders=..., Signature=...` |
| `Content-Length` | Conditional | Required for PUT/POST with body. Size in bytes. |
| `Content-Type` | No | MIME type of request body |
| `Content-MD5` | No | Base64-encoded 128-bit MD5 of body (per RFC 1864). Required for Object Lock retention uploads. |
| `Date` | Conditional | RFC 7231 format: `Wed, 01 Mar 2006 12:00:00 GMT`. Overridden by `x-amz-date`. |
| `Expect` | No | `100-continue` — server sends 100 Continue or error before client sends body |
| `Host` | Yes | Bucket endpoint. Path-style: `s3.{region}.amazonaws.com`. Virtual-hosted: `{bucket}.s3.{region}.amazonaws.com`. |
| `x-amz-content-sha256` | Required for SigV4 | SHA256 hex of payload, or `UNSIGNED-PAYLOAD`, or `STREAMING-AWS4-HMAC-SHA256-PAYLOAD` |
| `x-amz-date` | Conditional | ISO 8601 basic: `20170210T120000Z`. Takes precedence over `Date`. |
| `x-amz-security-token` | Conditional | Required for temporary credentials (STS) |
| `x-amz-expected-bucket-owner` | No | Account ID. Returns 403 if bucket owned by different account. |
| `x-amz-request-payer` | No | `requester` for Requester Pays buckets |

### 1.4 Common Response Headers

| Header | Description |
|--------|-------------|
| `x-amz-request-id` | Unique request identifier for debugging |
| `x-amz-id-2` | Extended request ID (host-level tracking) |
| `Date` | Response timestamp (RFC 7231) |
| `Server` | `AmazonS3` |
| `Content-Length` | Body length in bytes |
| `Content-Type` | Response MIME type |
| `Connection` | `open` or `close` |
| `ETag` | Quoted entity tag |
| `Last-Modified` | Object modification time (RFC 7231) |
| `Accept-Ranges` | Always `bytes` |
| `Content-Range` | `bytes start-end/total` (range requests only) |
| `Cache-Control` | Caching directives |
| `Content-Disposition` | Presentation info |
| `Content-Encoding` | Applied encoding |
| `Content-Language` | Content language |
| `Expires` | Expiration date |
| `x-amz-version-id` | Version ID (versioned buckets only) |
| `x-amz-delete-marker` | `true` if delete marker (header omitted entirely if false) |
| `x-amz-storage-class` | Storage class (omitted for STANDARD) |
| `x-amz-server-side-encryption` | `AES256`, `aws:kms`, or `aws:kms:dsse` |
| `x-amz-server-side-encryption-aws-kms-key-id` | KMS key ARN |
| `x-amz-server-side-encryption-context` | Base64-encoded JSON KMS encryption context |
| `x-amz-server-side-encryption-bucket-key-enabled` | `true` or `false` |
| `x-amz-server-side-encryption-customer-algorithm` | `AES256` (SSE-C echo) |
| `x-amz-server-side-encryption-customer-key-MD5` | Base64 MD5 of SSE-C key (echo) |
| `x-amz-expiration` | Lifecycle expiry: `expiry-date="Fri, 21 Dec 2012 00:00:00 GMT", rule-id="rule1"` |
| `x-amz-restore` | `ongoing-request="true"` or `ongoing-request="false", expiry-date="..."` |
| `x-amz-replication-status` | `COMPLETE`, `PENDING`, `FAILED`, `REPLICA` |
| `x-amz-request-charged` | `requester` (only when requester was charged) |
| `x-amz-object-lock-mode` | `GOVERNANCE` or `COMPLIANCE` |
| `x-amz-object-lock-retain-until-date` | ISO 8601 |
| `x-amz-object-lock-legal-hold` | `ON` or `OFF` |
| `x-amz-tagging-count` | Number of tags on the object |
| `x-amz-mp-parts-count` | Part count (only when `partNumber` query param specified) |
| `x-amz-website-redirect-location` | Website redirect URL |
| `x-amz-checksum-crc32` | Base64 CRC32 (if stored and checksum mode enabled) |
| `x-amz-checksum-crc32c` | Base64 CRC32C |
| `x-amz-checksum-sha1` | Base64 SHA-1 |
| `x-amz-checksum-sha256` | Base64 SHA-256 |

### 1.5 Error Response Format

All error responses (except HEAD requests) return XML with
`Content-Type: application/xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Error>
  <Code>NoSuchKey</Code>
  <Message>The specified key does not exist.</Message>
  <Resource>/mybucket/mykey</Resource>
  <RequestId>4442587FB7D0A2F9</RequestId>
</Error>
```

| Element | Type | Description |
|---------|------|-------------|
| `Code` | String | Machine-readable error identifier. Always present. |
| `Message` | String | Human-readable English description. Always present. |
| `Resource` | String | The bucket or object path that caused the error |
| `RequestId` | String | Request ID for correlation |

Some errors include additional elements: `BucketName`, `Region`, `Endpoint`,
`ExpectedDigest`, `CalculatedDigest`, `HostId`.

HEAD requests NEVER return an error body (HTTP spec). Error details must be
inferred from the status code and response headers alone.

### 1.6 Error Code Reference

#### 4xx Client Errors

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `AccessDenied` | 403 | Access denied |
| `BadDigest` | 400 | Content-MD5 did not match what the server received |
| `BucketAlreadyExists` | 409 | Bucket name taken by another account (globally unique namespace) |
| `BucketAlreadyOwnedByYou` | 409 | You already own this bucket (exception: us-east-1 returns 200) |
| `BucketNotEmpty` | 409 | Bucket still contains objects, versions, or delete markers |
| `ConditionalRequestConflict` | 409 | Race condition on conditional write; fetch ETag and retry |
| `EntityTooSmall` | 400 | Multipart part smaller than 5 MB minimum |
| `EntityTooLarge` | 400 | Upload exceeds maximum allowed size |
| `IncompleteBody` | 400 | Number of bytes sent did not match Content-Length header |
| `InvalidArgument` | 400 | Invalid argument (generic; many sub-cases) |
| `InvalidBucketName` | 400 | Bucket name violates naming rules |
| `InvalidBucketState` | 409 | Operation not valid for current bucket state |
| `InvalidDigest` | 400 | Content-MD5 or checksum value is not valid |
| `InvalidLocationConstraint` | 400 | Region string is not valid |
| `InvalidObjectState` | 403 | Object is archived (Glacier/Deep Archive); must restore first |
| `InvalidPart` | 400 | Part not found or its ETag does not match |
| `InvalidPartOrder` | 400 | Parts not listed in ascending PartNumber order |
| `InvalidRange` | 416 | Requested range cannot be satisfied |
| `InvalidRequest` | 400 | Generic invalid request |
| `InvalidStorageClass` | 400 | Specified storage class is not valid |
| `KeyTooLongError` | 400 | Key exceeds 1024 bytes (UTF-8) |
| `MalformedXML` | 400 | XML not well-formed or invalid against schema |
| `MaxMessageLengthExceeded` | 400 | Request was too large |
| `MetadataTooLarge` | 400 | Metadata headers exceed 2KB limit |
| `MethodNotAllowed` | 405 | Method not allowed against this resource |
| `MissingContentLength` | 411 | Must provide Content-Length HTTP header |
| `MissingSecurityHeader` | 400 | Required SSE-C headers missing |
| `NoSuchBucket` | 404 | Bucket does not exist |
| `NoSuchKey` | 404 | Object key does not exist |
| `NoSuchUpload` | 404 | Multipart upload not found (completed or aborted) |
| `NoSuchVersion` | 404 | Version ID does not match an existing version |
| `ObjectNotInActiveTierError` | 403 | Source object is archived (CopyObject) |
| `OperationAborted` | 409 | Conflicting conditional operation in progress; retry |
| `PreconditionFailed` | 412 | Conditional header check failed |
| `RequestTimeTooSkewed` | 403 | Time difference between request and server is too large |
| `SignatureDoesNotMatch` | 403 | Request signature does not match calculated value |
| `TooManyBuckets` | 400 | Account bucket limit exceeded (default: 100) |

#### 3xx Redirects

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `NotModified` | 304 | Conditional GET: resource not modified |
| `PermanentRedirect` | 301 | Bucket must be addressed at different endpoint. Includes `Endpoint` element. |
| `TemporaryRedirect` | 307 | Redirect to correct region endpoint |

#### 5xx Server Errors

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `InternalError` | 500 | Internal error; retry the request |
| `ServiceUnavailable` | 503 | Service unable to handle request; retry with backoff |
| `SlowDown` | 503 | Reduce request rate |

### 1.7 Data Types and Formats

#### Timestamps

| Context | Format | Example |
|---------|--------|---------|
| `x-amz-date` header (SigV4 signing) | ISO 8601 basic | `20170210T120000Z` |
| XML response bodies | ISO 8601 with milliseconds, UTC | `2009-10-12T17:50:30.000Z` |
| HTTP headers (`Date`, `Last-Modified`, conditionals) | RFC 7231 | `Thu, 01 Dec 2009 16:00:00 GMT` |
| Object Lock dates | ISO 8601 | `2025-12-31T23:59:59.000Z` |
| `x-amz-expiration` value | Embedded RFC 7231 | `expiry-date="Fri, 21 Dec 2012 00:00:00 GMT", rule-id="rule1"` |

#### ETag Format

| Scenario | Format | Example |
|----------|--------|---------|
| Single-part, no encryption | `"` + hex(MD5(bytes)) + `"` | `"d41d8cd98f00b204e9800998ecf8427e"` (empty object) |
| Multipart | `"` + hex(MD5(concat(part_md5_bytes...))) + `-` + part_count + `"` | `"a54357aff0632cce46d942af68356b38-3"` |
| SSE-C or SSE-KMS | Opaque; NOT an MD5 digest | Quoted hex string, no `-N` suffix |

The quotes are part of the ETag string value. When comparing ETags (for
If-Match, etc.), compare the full quoted string.

#### XML Encoding Rules for Keys in XML Bodies

| Character | XML Encoding |
|-----------|-------------|
| `&` | `&amp;` |
| `<` | `&lt;` |
| `>` | `&gt;` |
| `"` | `&quot;` |
| `'` | `&apos;` |
| `\r` (carriage return) | `&#13;` or `&#x0D;` |
| `\n` (newline) | `&#10;` or `&#x0A;` |

### 1.8 Conditional Request Headers

| Header | Condition | Success | Failure |
|--------|-----------|---------|---------|
| `If-Match` | Object ETag equals given value | 200 OK | 412 Precondition Failed |
| `If-None-Match` | Object ETag does NOT equal given value | 200 OK | 304 Not Modified (GET/HEAD) |
| `If-Modified-Since` | Object modified after given date | 200 OK | 304 Not Modified |
| `If-Unmodified-Since` | Object NOT modified after given date | 200 OK | 412 Precondition Failed |

#### Evaluation Precedence (critical for correct implementation)

When multiple conditional headers are present, evaluate in this order with
these interaction rules:

```
Step 1: If-Match (if present)
  ETag does NOT match → return 412 Precondition Failed (stop)
  ETag matches → pass, SKIP If-Unmodified-Since entirely

Step 2: If-Unmodified-Since (if present AND If-Match was absent)
  Object modified after date → return 412 Precondition Failed (stop)
  Object not modified after date → pass

Step 3: If-None-Match (if present)
  ETag DOES match → return 304 Not Modified for GET/HEAD (stop)
  ETag does not match → pass, SKIP If-Modified-Since entirely

Step 4: If-Modified-Since (if present AND If-None-Match was absent)
  Object NOT modified after date → return 304 Not Modified (stop)
  Object modified after date → pass
```

Key rules:

- `If-Match` takes precedence over `If-Unmodified-Since`
- `If-None-Match` takes precedence over `If-Modified-Since`
- When `If-Match` succeeds, `If-Unmodified-Since` is skipped
- When `If-None-Match` does not match (ETags differ), `If-Modified-Since`
  is skipped

#### Copy Operation Conditional Headers

For CopyObject and UploadPartCopy, conditional headers are evaluated against
the **source** object and use different header names:

| Copy Header | Equivalent | Failure |
|-------------|-----------|---------|
| `x-amz-copy-source-if-match` | `If-Match` | 412 |
| `x-amz-copy-source-if-none-match` | `If-None-Match` | 412 |
| `x-amz-copy-source-if-modified-since` | `If-Modified-Since` | 412 |
| `x-amz-copy-source-if-unmodified-since` | `If-Unmodified-Since` | 412 |

Same precedence rules apply.

#### Conditional Writes (PutObject)

- `If-None-Match: *` prevents overwriting. Returns 412 if key already exists.
  Enables atomic "create if not exists" semantics.
- `If-Match` (with an ETag value) prevents writing unless current ETag matches.

#### Format Requirements

| Header | Value Format | Example |
|--------|-------------|---------|
| `If-Match` | Quoted ETag | `"fba9dede5f27731c9771645a39863328"` |
| `If-None-Match` | Quoted ETag or `*` | `"fba9dede..."` or `*` |
| `If-Modified-Since` | RFC 7231 date | `Wed, 01 Mar 2006 12:00:00 GMT` |
| `If-Unmodified-Since` | RFC 7231 date | `Wed, 01 Mar 2006 12:00:00 GMT` |

### 1.9 Bucket Naming Rules

| Rule | Constraint |
|------|-----------|
| Length | 3-63 characters |
| Allowed characters | Lowercase letters (`a-z`), digits (`0-9`), hyphens (`-`), periods (`.`) |
| Must begin with | Lowercase letter or digit |
| Must end with | Lowercase letter or digit |
| No adjacent periods | `..` is forbidden |
| Not IP format | e.g., `192.168.5.4` |
| Cannot start with | `xn--`, `sthree-`, `amzn-s3-demo-` |
| Cannot end with | `-s3alias`, `--ol-s3`, `.mrap`, `--x-s3`, `--table-s3` |
| Uniqueness | Globally unique within partition (`aws`, `aws-cn`, `aws-us-gov`) |
| Immutability | Name and region cannot change after creation |
| Transfer Acceleration | Cannot contain periods (`.`) if using Transfer Acceleration |
| Security | Must not contain sensitive information (names appear in URLs) |

### 1.10 Object Key Rules

| Rule | Constraint |
|------|-----------|
| Maximum length | 1024 bytes (UTF-8 encoded) |
| Case sensitivity | Case-sensitive (`foo.txt` and `Foo.txt` are different keys) |
| Safe characters | `0-9`, `a-z`, `A-Z`, `!`, `-`, `_`, `.`, `*`, `'`, `(`, `)` |
| Characters requiring URL encoding | `&`, `$`, `@`, `=`, `;`, `/`, `:`, `+`, ` ` (space), `,`, `?` |
| Characters to avoid | `\`, `{`, `^`, `}`, `%`, `` ` ``, `]`, `"`, `>`, `[`, `~`, `<`, `#`, `\|` |
| Sort order | Lexicographic by UTF-8 byte values |

### 1.11 User Metadata Rules

| Rule | Constraint |
|------|-----------|
| Header prefix | `x-amz-meta-{name}` |
| Key storage | Stored in lowercase |
| Total size limit | 2 KB (sum of all UTF-8-encoded key names + values, not counting the `x-amz-meta-` prefix) |
| PUT header total limit | 8 KB maximum for all PUT request headers combined |
| Character support | US-ASCII recommended for REST |
| Immutable after upload | Cannot modify metadata after upload; must copy object with new metadata or re-PUT |
| Same-name headers | Merged into comma-delimited list (key comparison is case-insensitive) |

### 1.12 Storage Classes

| Storage Class | Code | Notes |
|---------------|------|-------|
| Standard | `STANDARD` | Default. **Omitted from response headers** when STANDARD. |
| Reduced Redundancy | `REDUCED_REDUNDANCY` | Lower durability |
| Standard Infrequent Access | `STANDARD_IA` | Min 128KB charge, 30-day min |
| One Zone Infrequent Access | `ONEZONE_IA` | Single AZ |
| Intelligent Tiering | `INTELLIGENT_TIERING` | Automatic cost optimization |
| Glacier Flexible Retrieval | `GLACIER` | Minutes to hours retrieval |
| Glacier Deep Archive | `DEEP_ARCHIVE` | 12-48 hour retrieval |
| Glacier Instant Retrieval | `GLACIER_IR` | Millisecond retrieval |
| S3 Express One Zone | `EXPRESS_ONEZONE` | Single-digit ms latency, directory buckets |
| Outposts | `OUTPOSTS` | S3 on Outposts only |
| Snow | `SNOW` | AWS Snow devices only |

---

## 2. Bucket Operations

### 2.1 CreateBucket

**Request:** `PUT /{bucket}`

**Request Headers (operation-specific):**

| Header | Required | Values |
|--------|----------|--------|
| `x-amz-acl` | No | `private`, `public-read`, `public-read-write`, `authenticated-read` |
| `x-amz-grant-full-control` | No | `id="CanonicalUserId"` |
| `x-amz-grant-read` | No | `id="CanonicalUserId"` |
| `x-amz-grant-read-acp` | No | `id="CanonicalUserId"` |
| `x-amz-grant-write` | No | `id="CanonicalUserId"` |
| `x-amz-grant-write-acp` | No | `id="CanonicalUserId"` |
| `x-amz-bucket-object-lock-enabled` | No | `true`, `false`. Enables Object Lock and versioning. |
| `x-amz-object-ownership` | No | `BucketOwnerPreferred`, `ObjectWriter`, `BucketOwnerEnforced` (default) |

**Request Body (optional):**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<CreateBucketConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
    <LocationConstraint>us-west-2</LocationConstraint>
</CreateBucketConfiguration>
```

| Element | Type | Required | Description |
|---------|------|----------|-------------|
| `CreateBucketConfiguration` | Container | Root | Required if specifying sub-elements |
| `LocationConstraint` | String | No | AWS Region code. Omit or leave empty for `us-east-1`. Special value `EU` maps to `eu-west-1`. |

If no body is provided, or if body has no `LocationConstraint`, bucket is
created in `us-east-1`.

**Response:** `200 OK`

**Response Headers:**

| Header | Description |
|--------|-------------|
| `Location` | `/{bucket-name}` |

**Response Body:** Empty on success.

**Error Codes:**

| Code | HTTP Status | Condition |
|------|-------------|-----------|
| `InvalidBucketName` | 400 | Name violates naming rules |
| `InvalidLocationConstraint` | 400 | Region string is not valid |
| `IllegalLocationConstraintException` | 400 | Request sent to wrong regional endpoint |
| `TooManyBuckets` | 400 | Account limit exceeded (default 100) |
| `BucketAlreadyExists` | 409 | Name taken by a different account |
| `BucketAlreadyOwnedByYou` | 409 | You already own this bucket |

**Behavior Rules:**

1. **Empty body or missing `LocationConstraint`:** Bucket created in `us-east-1`.
2. **`LocationConstraint` = `EU`:** Bucket physically created in `eu-west-1`.
3. **SigV4 signing region for global endpoint:** When sending to
   `s3.amazonaws.com`, always use `us-east-1` as the signing region in the
   `Credential` field, regardless of the `LocationConstraint` value.
4. **Default ownership:** `BucketOwnerEnforced` (ACLs disabled). To use ACLs,
   set `x-amz-object-ownership` to `ObjectWriter` or `BucketOwnerPreferred`.
5. **Object Lock:** Setting `x-amz-bucket-object-lock-enabled: true`
   automatically enables versioning.

**Edge Cases:**

1. **`BucketAlreadyOwnedByYou` in `us-east-1`:** Returns `200 OK` (not 409)
   and resets the bucket ACL. This is legacy behavior unique to us-east-1.
   All other regions return 409.
2. **Public ACL creation:** Cannot create bucket with public ACL in a single
   request. Must create, then delete public access block, then set ACL.

**MinIO Deviations:**

- `x-minio-force-create` header bypasses certain creation constraints.
- Returns `BucketAlreadyOwnedByYou` if bucket exists locally (no
  per-region distinction).
- Warns when bucket count exceeds threshold, but does not reject.

---

### 2.2 DeleteBucket

**Request:** `DELETE /{bucket}`

**Request Headers:** `x-amz-expected-bucket-owner` (optional).

**Request Body:** None.

**Response:** `204 No Content`. No body.

**Error Codes:**

| Code | HTTP Status | Condition |
|------|-------------|-----------|
| `NoSuchBucket` | 404 | Bucket does not exist |
| `BucketNotEmpty` | 409 | Contains objects, versions, or delete markers |
| `AccessDenied` | 403 | Insufficient permissions or owner mismatch |

**Behavior Rules:**

1. **Bucket must be completely empty:** All objects, all object versions (for
   versioned buckets), and all delete markers must be deleted first.
2. **After deletion:** Bucket name becomes available for reuse by any account.
3. **Irreversible:** No confirmation prompt.

**Edge Cases:**

1. **Versioned buckets:** `DeleteObject` creates a delete marker instead of
   deleting. Must explicitly delete each version by version ID.
2. **Lifecycle-managed buckets:** Objects pending expiration may prevent
   deletion until processed.

**MinIO Deviations:**

- `x-minio-force-delete` header force-deletes (needs `ForceDeleteBucketAction`).
- Force delete blocked if bucket has Object Lock or active replication rules.

---

### 2.3 HeadBucket

**Request:** `HEAD /{bucket}`

**Request Headers:** `x-amz-expected-bucket-owner` (optional).

**Request Body:** None.

**Response:** `200 OK`. **No body ever** (HEAD spec).

**Response Headers:**

| Header | Always Present | Description |
|--------|----------------|-------------|
| `x-amz-bucket-region` | **Yes, even on 403/404** | Region where bucket is located |
| `x-amz-access-point-alias` | Yes | `true` if bucket name is access point alias |

**Status Codes:**

| HTTP Status | Condition |
|-------------|-----------|
| 200 OK | Bucket exists and you have access |
| 301 Moved Permanently | Bucket in different region. `x-amz-bucket-region` tells correct region. |
| 403 Forbidden | Access denied. **`x-amz-bucket-region` still returned.** |
| 404 Not Found | Bucket does not exist |

**Key Rules:**

1. **No response body on ANY status.** Error details inferred from status code
   and headers only.
2. **Region always disclosed:** `x-amz-bucket-region` returned even on 403
   and 404. This is intentional for region discovery.
3. **Region-agnostic routing:** Can send HeadBucket to any regional endpoint
   and it will respond about any bucket in the partition.
4. **Permission:** Requires `s3:ListBucket` (not `s3:HeadBucket`).

**MinIO Deviations:**

- Checks `HeadBucketAction` first; falls back to `ListBucketAction`.
- Does not return `x-amz-bucket-region` header.

---

### 2.4 ListBuckets

**Request:** `GET /`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `bucket-region` | string | - | Filter to buckets in this region |
| `prefix` | string | - | Filter by name prefix |
| `continuation-token` | string | - | Opaque pagination token (0-1024 chars) |
| `max-buckets` | integer | 10000 | Max results (1-10000) |

**Response:** `200 OK`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
    <Owner>
        <ID>canonical-user-id</ID>
        <DisplayName>display-name</DisplayName>
    </Owner>
    <Buckets>
        <Bucket>
            <Name>my-bucket</Name>
            <CreationDate>2006-02-03T16:45:09.000Z</CreationDate>
            <BucketRegion>us-east-1</BucketRegion>
        </Bucket>
    </Buckets>
    <ContinuationToken>opaque-token</ContinuationToken>
    <Prefix>my</Prefix>
</ListAllMyBucketsResult>
```

| Element | Type | Presence | Description |
|---------|------|----------|-------------|
| `ListAllMyBucketsResult` | Container | Always | Root element |
| `Owner` | Container | Always | Bucket owner |
| `Owner/ID` | String | Always | Canonical user ID (64-char hex) |
| `Owner/DisplayName` | String | Always | Display name |
| `Buckets` | Container | Always | Container for Bucket elements. Empty `<Buckets/>` if none. |
| `Bucket/Name` | String | Always in Bucket | Bucket name |
| `Bucket/CreationDate` | Timestamp | Always in Bucket | ISO 8601 with ms: `YYYY-MM-DDTHH:MM:SS.000Z` |
| `Bucket/BucketRegion` | String | Paginated responses | AWS Region |
| `ContinuationToken` | String | Only if more results | Opaque token for next page |
| `Prefix` | String | If prefix was in request | Echo of prefix filter |

**Behavior Rules:**

1. Returns all buckets across all regions unless `bucket-region` filter set.
2. Sorted alphabetically by name.
3. No `ContinuationToken` in response = all results returned.
4. Empty result: Valid XML with `<Buckets/>`, not an error.
5. Permission: `s3:ListAllMyBuckets`.

**MinIO Deviations:**

- **No pagination:** Returns all buckets in one response.
- No `continuation-token`, `max-buckets`, `prefix`, or `bucket-region` support.
- Filters by IAM permissions when `ListAllMyBuckets` denied.

---

### 2.5 GetBucketLocation

**Request:** `GET /{bucket}?location`

The `location` query parameter has no value — its presence alone identifies
the operation.

**Request Headers:** `x-amz-expected-bucket-owner` (optional).

**Response:** `200 OK`

**For a bucket in `us-west-2`:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/">us-west-2</LocationConstraint>
```

**For a bucket in `us-east-1` (CRITICAL EDGE CASE):**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/"/>
```

The `LocationConstraint` element is **self-closing/empty**, not absent.
Clients must treat empty/null as `us-east-1`.

**For a bucket created with `LocationConstraint` = `EU`:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/">EU</LocationConstraint>
```

Returns `EU`, not `eu-west-1`. The value mirrors what was provided at creation.

**Error Codes:**

| Code | HTTP Status | Condition |
|------|-------------|-----------|
| `NoSuchBucket` | 404 | Bucket does not exist |
| `AccessDenied` | 403 | Insufficient permissions |

**Behavior Rules:**

1. `us-east-1` returns empty `LocationConstraint` (not missing, not text).
2. `EU` preserved verbatim from creation.
3. Deprecated — use HeadBucket's `x-amz-bucket-region` header instead.
4. Region is immutable for a bucket's lifetime.

**MinIO Deviations:**

- Region is server-wide, not per-bucket. All buckets in same "region".
- Returns empty element for default region (matches us-east-1 behavior).

---

## 3. Object Operations

### 3.1 PutObject

**Request:** `PUT /{bucket}/{key}`

**Request Body:** Raw binary object data.

**Request Headers:**

#### Content Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Length` | Yes (unless chunked) | Size of body in bytes |
| `Content-Type` | No | MIME type. Default: `application/octet-stream` |
| `Content-MD5` | No | Base64-encoded 128-bit MD5 digest. S3 validates and rejects with `400 BadDigest` on mismatch. Required for Object Lock retention uploads. |
| `Content-Encoding` | No | e.g., `gzip` |
| `Content-Disposition` | No | e.g., `attachment; filename="file.txt"` |
| `Content-Language` | No | e.g., `en-US` |
| `Cache-Control` | No | e.g., `max-age=3600` |
| `Expires` | No | RFC 7234 expiration |
| `Expect` | No | `100-continue`. Server sends `100 Continue` before client sends body, allowing early error detection without transmitting full body. |

#### User Metadata

| Header | Description |
|--------|-------------|
| `x-amz-meta-{name}` | Arbitrary user metadata. Total limit 2 KB. Names lowercased by S3. |

#### Storage and ACL

| Header | Values |
|--------|--------|
| `x-amz-storage-class` | `STANDARD` (default), `REDUCED_REDUNDANCY`, `STANDARD_IA`, `ONEZONE_IA`, `INTELLIGENT_TIERING`, `GLACIER`, `DEEP_ARCHIVE`, `GLACIER_IR`, `EXPRESS_ONEZONE`, `OUTPOSTS`, `SNOW` |
| `x-amz-acl` | `private`, `public-read`, `public-read-write`, `authenticated-read`, `bucket-owner-read`, `bucket-owner-full-control` |
| `x-amz-grant-read` | Grantee string |
| `x-amz-grant-read-acp` | Grantee string |
| `x-amz-grant-write-acp` | Grantee string |
| `x-amz-grant-full-control` | Grantee string |

#### Server-Side Encryption

| Header | Description |
|--------|-------------|
| `x-amz-server-side-encryption` | `AES256` (SSE-S3), `aws:kms` (SSE-KMS), or `aws:kms:dsse` |
| `x-amz-server-side-encryption-aws-kms-key-id` | KMS key ARN. Defaults to `aws/s3` managed key if omitted with `aws:kms`. |
| `x-amz-server-side-encryption-context` | Base64-encoded JSON KMS encryption context |
| `x-amz-server-side-encryption-bucket-key-enabled` | `true`/`false`. Use S3 Bucket Key to reduce KMS calls. |
| `x-amz-server-side-encryption-customer-algorithm` | Must be `AES256` (SSE-C) |
| `x-amz-server-side-encryption-customer-key` | Base64-encoded 256-bit key (SSE-C) |
| `x-amz-server-side-encryption-customer-key-MD5` | Base64-encoded MD5 of the key (SSE-C) |

SSE-C requires all three customer headers together. Cannot mix SSE-C with
SSE-KMS in the same request.

#### Conditional Writes

| Header | Behavior |
|--------|----------|
| `If-None-Match` | Value `*`. Returns 412 if key already exists. |
| `If-Match` | Returns 412 if current ETag does not match. |

#### Tagging and Object Lock

| Header | Description |
|--------|-------------|
| `x-amz-tagging` | URL-encoded tags: `key1=val1&key2=val2` |
| `x-amz-object-lock-mode` | `GOVERNANCE` or `COMPLIANCE` |
| `x-amz-object-lock-retain-until-date` | ISO 8601 |
| `x-amz-object-lock-legal-hold` | `ON` or `OFF` |

#### Checksums (provide at most one)

| Header | Algorithm |
|--------|-----------|
| `x-amz-checksum-crc32` | Base64 CRC32 |
| `x-amz-checksum-crc32c` | Base64 CRC32C |
| `x-amz-checksum-sha1` | Base64 SHA-1 |
| `x-amz-checksum-sha256` | Base64 SHA-256 |
| `x-amz-sdk-checksum-algorithm` | Declares which: `CRC32`, `CRC32C`, `SHA1`, `SHA256` |

#### Other

| Header | Description |
|--------|-------------|
| `x-amz-website-redirect-location` | Redirect URL for website hosting |
| `x-amz-request-payer` | `requester` |
| `x-amz-expected-bucket-owner` | Account ID |

**Response:** `200 OK`

**Response Headers:**

| Header | Description |
|--------|-------------|
| `ETag` | Quoted hex MD5 for unencrypted single-part. See ETag calculation section. |
| `x-amz-version-id` | Version ID if versioning enabled |
| `x-amz-server-side-encryption` | Echo of encryption algorithm |
| `x-amz-server-side-encryption-aws-kms-key-id` | Echo of KMS key |
| `x-amz-server-side-encryption-customer-algorithm` | Echo: `AES256` |
| `x-amz-server-side-encryption-customer-key-MD5` | Echo of key MD5 |
| `x-amz-expiration` | Lifecycle expiry if a rule applies |
| `x-amz-request-charged` | `requester` if Requester Pays |

**Status Codes:**

| Code | Meaning |
|------|---------|
| 200 OK | Object stored successfully |
| 400 Bad Request | Invalid params, `BadDigest`, invalid encryption |
| 403 Forbidden | Access denied, bucket owner mismatch |
| 409 ConditionalRequestConflict | Race condition on conditional write |
| 412 Precondition Failed | `If-Match`/`If-None-Match` condition not met |

**Behavior Rules:**

1. **Atomicity:** S3 never stores partial objects. A 200 means the entire
   object is durably stored.
2. **Last-writer-wins:** Without versioning, concurrent PUTs result in one
   winner (nondeterministic). With versioning, all writes are separate versions.
3. **`Expect: 100-continue`:** Server MUST either send `100 Continue` or a
   final error response; it must not silently wait.
4. **Content-MD5 validation:** Base64-encoded (NOT hex). Mismatch → `400 BadDigest`.
5. **ETag for unencrypted single-part:** `"` + hex(MD5(object_bytes)) + `"`.
   Quotes are part of the value.
6. **Empty objects valid.** Content-Length 0 creates a zero-byte object.
7. **Max single PUT:** 5 GB. Use multipart for larger.

---

### 3.2 GetObject

**Request:** `GET /{bucket}/{key}`

**Request Body:** None.

**Response Body:** Raw binary object bytes (or partial for range requests).

**Query Parameters:**

| Parameter | Description |
|-----------|-------------|
| `versionId` | Retrieve specific version |
| `partNumber` | Retrieve specific part of multipart object (1-10000) |
| `response-content-type` | Override `Content-Type` in response |
| `response-content-disposition` | Override `Content-Disposition` |
| `response-content-encoding` | Override `Content-Encoding` |
| `response-content-language` | Override `Content-Language` |
| `response-cache-control` | Override `Cache-Control` |
| `response-expires` | Override `Expires` |

`response-*` overrides: Only work on `200 OK`, only with signed requests.
Silently ignored on 206, 304, errors, and anonymous requests.

**Request Headers:**

| Header | Description |
|--------|-------------|
| `If-Match` | Return only if ETag matches. Otherwise 412. |
| `If-None-Match` | Return only if ETag differs. Otherwise 304. |
| `If-Modified-Since` | Return only if modified after date. Otherwise 304. |
| `If-Unmodified-Since` | Return only if NOT modified after date. Otherwise 412. |
| `Range` | `bytes=start-end` (inclusive both). `bytes=start-`. `bytes=-N` (last N bytes). |
| `x-amz-checksum-mode` | `ENABLED` to include stored checksums in response headers |
| SSE-C headers | Required if object stored with SSE-C |

S3 supports only a single byte range per request (no multi-range). Invalid
range → `416 Range Not Satisfiable`.

**Response Headers:**

| Header | Description |
|--------|-------------|
| `Content-Length` | Bytes in body. For range: range length, not total. |
| `Content-Type` | MIME type (or `response-content-type` override) |
| `Content-Range` | `bytes 0-9/443` (only on 206) |
| `Accept-Ranges` | Always `bytes` |
| `ETag` | Quoted entity tag |
| `Last-Modified` | RFC 7231 timestamp |
| `x-amz-version-id` | Present if versioning is/was enabled |
| `x-amz-delete-marker` | `true` if current version is delete marker (status 404) |
| `x-amz-storage-class` | Not returned for STANDARD; absent = STANDARD |
| `x-amz-tagging-count` | Number of tags |
| `x-amz-mp-parts-count` | Only with `partNumber` query param |
| `x-amz-restore` | Restore status for archived objects |
| All `x-amz-meta-*` | User-defined metadata |
| All `x-amz-server-side-encryption-*` | Encryption details |
| All `x-amz-object-lock-*` | Object Lock details |
| All `x-amz-checksum-*` | Checksums (when checksum mode enabled) |

**Status Codes:**

| Code | Meaning |
|------|---------|
| 200 OK | Full object returned |
| 206 Partial Content | Range request satisfied |
| 304 Not Modified | Conditional: ETag matched `If-None-Match`, or not modified since |
| 403 Forbidden | Access denied. Also `InvalidObjectState` for archived objects. Also returned instead of 404 when caller lacks `s3:ListBucket` (prevents key enumeration). |
| 404 Not Found | Object does not exist (only when caller has `s3:ListBucket`). Also for delete markers with `x-amz-delete-marker: true`. |
| 405 Method Not Allowed | GET on specific `versionId` that is a delete marker |
| 412 Precondition Failed | `If-Match` or `If-Unmodified-Since` condition not met |
| 416 Range Not Satisfiable | Range exceeds object size. `Content-Range: bytes */total_size`. |

**Behavior Rules:**

1. **Range → 206, not 200.** `Content-Length` = range size. `Content-Range`
   always included.
2. **Only single ranges.** Multi-range requests are invalid.
3. **`response-*` overrides only on 200.** Not on 206, 304, or errors.
4. **403 vs 404:** Without `s3:ListBucket`, missing objects return 403 to
   prevent key enumeration.
5. **Archived objects:** Glacier/Deep Archive → `403 InvalidObjectState`.
   Must `RestoreObject` first.
6. **Delete markers with versioning:**
   - GET without versionId, current = delete marker: `404` with
     `x-amz-delete-marker: true`.
   - GET with versionId pointing to delete marker: `405 Method Not Allowed`.
7. **`partNumber`:** Returns single part of multipart object. Response
   includes `x-amz-mp-parts-count` and `Content-Range`. Status 206.
8. **`x-amz-storage-class` absent = STANDARD.**

---

### 3.3 HeadObject

**Request:** `HEAD /{bucket}/{key}`

Same query parameters and request headers as GetObject. Same response headers
as GetObject. **No response body ever.**

**Key Differences from GetObject:**

1. **No body on ANY status.** Not even error bodies. Cannot distinguish
   different 403 causes.
2. **All metadata returned:** All `x-amz-meta-*` headers are returned. Primary
   use case: retrieve metadata without downloading.
3. **Archived objects return 200 with metadata** (unlike GetObject which
   returns 403). Includes `x-amz-restore` status for checking restore progress.
4. **Range on HEAD:** Affects `Content-Length` (reports range size). Valid
   range → 206. Unsatisfiable → 416.
5. **`Content-Length` always reflects what body WOULD contain.** Full object
   size for non-range, range size for range request.
6. **Conditional logic identical** to GetObject with same precedence rules.

**Status Codes:** Same as GetObject: 200, 304, 403, 404, 405, 412, 416.

---

### 3.4 DeleteObject

**Request:** `DELETE /{bucket}/{key}`

**Query Parameters:** `versionId` (optional — delete specific version permanently).

**Request Body:** None.

**Request Headers:**

| Header | Description |
|--------|-------------|
| `x-amz-mfa` | `{serial-number} {auth-code}` (space-separated). Required for permanent version delete when MFA Delete enabled. Must use HTTPS. |
| `x-amz-bypass-governance-retention` | `true` to bypass Governance-mode Object Lock. Requires `s3:BypassGovernanceRetention`. Does not bypass Compliance mode. |
| `x-amz-expected-bucket-owner` | Account ID |
| `If-Match` | ETag conditional. 412 if mismatch. `*` matches any existing object. |

**Response:** `204 No Content`

**Response Headers:**

| Header | Description |
|--------|-------------|
| `x-amz-delete-marker` | `true` if operation created or deleted a delete marker |
| `x-amz-version-id` | Version ID of created delete marker or permanently deleted version |
| `x-amz-request-charged` | `requester` |

**CRITICAL: Non-existent objects return `204`, NOT `404`.** DeleteObject is
idempotent by design.

**Versioning Behavior Matrix:**

| Bucket State | No versionId | With versionId |
|-------------|-------------|----------------|
| **Unversioned** | Object permanently deleted. 204. | N/A |
| **Versioned, object exists** | Delete marker created (previous versions kept). 204 + `x-amz-delete-marker: true` + `x-amz-version-id: {marker-id}`. | Specific version permanently deleted. 204 + `x-amz-version-id: {id}`. |
| **Versioned, object doesn't exist** | Delete marker still created. 204 + `x-amz-delete-marker: true`. | No-op. 204. |
| **Versioned, versionId is delete marker** | N/A | Delete marker removed ("undelete"). 204 + `x-amz-delete-marker: true` + `x-amz-version-id: {marker-id}`. |
| **Suspended, no versionId** | Null version replaced with delete marker. 204 + `x-amz-delete-marker: true`. | N/A |

**Object Lock Interactions:**

- **Compliance mode:** Cannot delete until retention expires. Returns 403.
- **Governance mode:** Can delete with `x-amz-bypass-governance-retention: true`
  and proper permission.
- **Legal hold:** Cannot delete while ON, regardless of mode.
- **Delete markers** are not subject to Object Lock.

---

### 3.5 CopyObject

**Request:** `PUT /{bucket}/{key}` with `x-amz-copy-source` header.

Same HTTP verb as PutObject — the presence of `x-amz-copy-source` distinguishes
a copy from an upload.

**No request body.** Source data specified via header.

**Request Headers:**

#### Required

| Header | Format | Description |
|--------|--------|-------------|
| `x-amz-copy-source` | `/{source-bucket}/{source-key}` | URL-encoded path. To copy specific version: `/{bucket}/{key}?versionId={id}`. Leading `/` required. |

#### Metadata Directive

| Header | Values | Default | Description |
|--------|--------|---------|-------------|
| `x-amz-metadata-directive` | `COPY`, `REPLACE` | `COPY` | `COPY`: metadata from source. `REPLACE`: metadata from this request only. |
| `x-amz-tagging-directive` | `COPY`, `REPLACE` | `COPY` | Same logic for tags. |

**Metadata directive nuances:**

- `COPY` mode: Content-Type, Cache-Control, Content-Disposition,
  Content-Encoding, Content-Language, Expires, and all `x-amz-meta-*` copied
  from source. Request headers for these are **ignored**.
- `REPLACE` mode: Only values from request headers used. Source metadata
  discarded entirely. Omitting a header = destination lacks it.
- `x-amz-storage-class` **always** honored from request regardless of directive.
- `x-amz-website-redirect-location` **never** copied even in COPY mode.
- ACLs **never** copied. Destination defaults to `private`.
- No merging in REPLACE mode.

#### Conditional Copy Headers (evaluated against SOURCE)

| Header | Behavior |
|--------|----------|
| `x-amz-copy-source-if-match` | Copy only if source ETag matches. Otherwise 412. |
| `x-amz-copy-source-if-none-match` | Copy only if source ETag differs. Otherwise 412. |
| `x-amz-copy-source-if-modified-since` | Copy only if source modified after date. Otherwise 412. |
| `x-amz-copy-source-if-unmodified-since` | Copy only if source NOT modified after date. Otherwise 412. |

#### Conditional Headers (evaluated against DESTINATION)

| Header | Behavior |
|--------|----------|
| `If-Match` | Copy only if destination ETag matches. 412 on mismatch. |
| `If-None-Match` | `*` only. Copy only if destination key doesn't exist. 412 if exists. |

#### SSE-C Source Decryption (required if source stored with SSE-C)

| Header | Description |
|--------|-------------|
| `x-amz-copy-source-server-side-encryption-customer-algorithm` | `AES256` |
| `x-amz-copy-source-server-side-encryption-customer-key` | Base64 key for SOURCE |
| `x-amz-copy-source-server-side-encryption-customer-key-MD5` | Base64 MD5 of SOURCE key |

Enables encryption key rotation: decrypt source with old key, re-encrypt
destination with new key, in a single CopyObject call.

All PutObject headers for destination encryption, ACL, storage class, tags,
Object Lock also accepted.

**Response:** `200 OK` with XML body:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<CopyObjectResult>
  <ETag>"9b2cf535f27731c974343645a3985328"</ETag>
  <LastModified>2009-10-12T17:50:30.000Z</LastModified>
</CopyObjectResult>
```

| Element | Type | Description |
|---------|------|-------------|
| `ETag` | String | Quoted hex hash of the new object |
| `LastModified` | Timestamp | ISO 8601 creation time of the copy |

Checksum elements (`ChecksumCRC32`, `ChecksumSHA256`, etc.) included if
source had checksums.

**Response Headers:** `x-amz-version-id` (destination), `x-amz-copy-source-version-id`
(source), encryption echoes, `x-amz-expiration`.

**CRITICAL: `200 OK` can contain an error in the body.** Under load or during
internal timeouts, S3 may return HTTP 200 with an `<Error>` XML body instead
of `<CopyObjectResult>`:

```xml
<Error>
  <Code>InternalError</Code>
  <Message>We encountered an internal error. Please try again.</Message>
  <RequestId>...</RequestId>
  <HostId>...</HostId>
</Error>
```

Implementations MUST check whether root XML element is `<Error>` or
`<CopyObjectResult>`.

**Status Codes:**

| Code | Meaning |
|------|---------|
| 200 OK | Copy succeeded (but parse body — may contain error!) |
| 400 Bad Request | Invalid directive, storage class, encryption |
| 403 Forbidden | Access denied. `ObjectNotInActiveTierError` if source archived. |
| 404 Not Found | Source object or bucket does not exist |
| 409 ConditionalRequestConflict | Race condition |
| 412 Precondition Failed | Any conditional header not met |

**Behavior Rules:**

1. **Size limit: 5 GB.** Use multipart with UploadPartCopy for larger.
2. **Copy-to-self is common.** Same bucket+key with `REPLACE` is the standard
   way to update metadata, change Content-Type, rotate encryption keys, or
   change storage class.
3. **Multipart source → single-part destination.** Copying a multipart object
   via CopyObject produces a single-part object with a new standard MD5 ETag.
4. **Archived objects cannot be copied.** Returns `403 ObjectNotInActiveTierError`.
5. **Version-specific copy:** `?versionId=xxx` in `x-amz-copy-source` copies
   specific version. Source is not affected.
6. **Cross-account:** Needs `s3:GetObject` on source, `s3:PutObject` on
   destination.

---

## 4. Multipart Upload Operations

### 4.1 CreateMultipartUpload

**Request:** `POST /{bucket}/{key}?uploads`

Identified by: bare `?uploads` query param (no value, no `uploadId`).

**Request Body:** Empty.

**Request Headers:** Accepts all PutObject metadata headers: Content-Type,
Cache-Control, Content-Disposition, Content-Encoding, Content-Language,
Expires, `x-amz-meta-*`, all encryption headers, ACL, storage class, tagging,
Object Lock, checksum algorithm. All metadata set at initiation, not on parts.

SSE-C headers set here must be repeated identically on every UploadPart and
UploadPartCopy call.

**Response:** `200 OK`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
   <Bucket>bucket-name</Bucket>
   <Key>object-key</Key>
   <UploadId>upload-id</UploadId>
</InitiateMultipartUploadResult>
```

| Element | Description |
|---------|-------------|
| `Bucket` | Bucket name |
| `Key` | Object key |
| `UploadId` | Opaque string. Must be used in all subsequent multipart calls. |

**Response Headers:** `x-amz-abort-date`, `x-amz-abort-rule-id` (lifecycle),
encryption echoes, `x-amz-checksum-algorithm`.

**Behavior Rules:**

1. `UploadId` is opaque — do not assume any format.
2. Multiple concurrent uploads for the same key are allowed, each with unique
   UploadId.
3. Uploaded parts incur storage charges until completed or aborted.
4. Lifecycle rules can auto-abort incomplete uploads after configured days.

---

### 4.2 UploadPart

**Request:** `PUT /{bucket}/{key}?partNumber={n}&uploadId={id}`

Identified by: PUT with `partNumber` + `uploadId`, NO `x-amz-copy-source`.

**Request Body:** Raw binary part data.

**Query Parameters:**

| Parameter | Type | Required | Range |
|-----------|------|----------|-------|
| `partNumber` | integer | Yes | 1-10,000 inclusive |
| `uploadId` | string | Yes | From CreateMultipartUpload |

**Request Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Length` | No | Size of body in bytes |
| `Content-MD5` | Conditional | Base64 MD5. Required for Object Lock. Validated → 400 BadDigest. |
| `x-amz-checksum-*` | No | Must match algorithm from CreateMultipartUpload |
| SSE-C headers | Conditional | Required if SSE-C set in CreateMultipartUpload. Must match exactly. |

**Response:** `200 OK`

**Response Headers:**

| Header | Description |
|--------|-------------|
| `ETag` | **Critical.** Entity tag for the uploaded part. Must be saved and provided verbatim in CompleteMultipartUpload. |

**Response Body:** Empty.

**Part Size Rules:**

| Constraint | Value | Enforced When |
|-----------|-------|---------------|
| All parts except last | Min **5 MB** (5,242,880 bytes) | At CompleteMultipartUpload |
| Last part | **No minimum** (can be 0 bytes) | N/A |
| Any part | Max **5 GB** (5,368,709,120 bytes) | At UploadPart |

Part size minimums are NOT validated at UploadPart time. The 5 MB minimum is
only checked at CompleteMultipartUpload.

**Behavior Rules:**

1. **Part number range:** 1-10,000. Outside → `InvalidArgument` (400).
2. **Overwrite:** Uploading same part number again overwrites previous data.
   New ETag replaces old.
3. **ETag value:** For non-encrypted, ETag = hex MD5 of part data. For SSE-C/KMS,
   computed differently. Regardless, store and echo back the exact string.
4. **Checksum consistency:** Must match algorithm from CreateMultipartUpload.

**Error Codes:**

| Code | Status | Cause |
|------|--------|-------|
| `NoSuchUpload` | 404 | Upload completed, aborted, or invalid |
| `InvalidArgument` | 400 | Part number outside 1-10,000 |
| `BadDigest` | 400 | Content-MD5 mismatch |

---

### 4.3 UploadPartCopy

**Request:** `PUT /{bucket}/{key}?partNumber={n}&uploadId={id}` with
`x-amz-copy-source` header.

Same URL as UploadPart — distinguished by presence of `x-amz-copy-source`.

**Request Body:** None. Data comes from source object.

**Key Request Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `x-amz-copy-source` | Yes | `/{bucket}/{key}` or `/{bucket}/{key}?versionId={id}` |
| `x-amz-copy-source-range` | No | `bytes=first-last` (zero-based, inclusive). Source must be > 5 MB for range copy. |
| `x-amz-copy-source-if-match` | No | Conditional on source ETag |
| `x-amz-copy-source-if-none-match` | No | Conditional on source ETag |
| `x-amz-copy-source-if-modified-since` | No | Conditional on source date |
| `x-amz-copy-source-if-unmodified-since` | No | Conditional on source date |
| Source SSE-C headers | Conditional | To decrypt source (if SSE-C) |
| Destination SSE-C headers | Conditional | Must match CreateMultipartUpload |

**Response:** `200 OK`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<CopyPartResult>
   <ETag>string</ETag>
   <LastModified>timestamp</LastModified>
</CopyPartResult>
```

Save the `ETag` for CompleteMultipartUpload.

**Error Codes:**

| Code | Status | Cause |
|------|--------|-------|
| `NoSuchUpload` | 404 | Invalid/completed/aborted upload |
| `NoSuchKey` | 404 | Source object does not exist |
| `InvalidRequest` | 400 | Source < 5 MB with range copy |
| `PreconditionFailed` | 412 | Conditional check failed |

---

### 4.4 CompleteMultipartUpload

**Request:** `POST /{bucket}/{key}?uploadId={id}`

Identified by: POST with `uploadId` (no bare `?uploads`).

**Request Body:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<CompleteMultipartUpload xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
   <Part>
      <PartNumber>1</PartNumber>
      <ETag>"a54357aff0632cce46d942af68356b38"</ETag>
   </Part>
   <Part>
      <PartNumber>2</PartNumber>
      <ETag>"0c78aef83f66abc1fa1e8477f296d394"</ETag>
   </Part>
</CompleteMultipartUpload>
```

| Element | Required | Description |
|---------|----------|-------------|
| `Part` | Yes (1+) | One per part. Must be ascending PartNumber. No duplicates. |
| `Part/PartNumber` | Yes | 1-10,000. Must be ascending. |
| `Part/ETag` | Yes | Exact ETag from UploadPart/UploadPartCopy response. |

**Not all uploaded parts must be included.** Unlisted parts are discarded and
their storage freed.

**Request Headers:**

| Header | Description |
|--------|-------------|
| `If-Match` | Complete only if existing object's ETag matches |
| `If-None-Match` | `*` — complete only if object doesn't exist |
| `x-amz-mp-object-size` | Expected total size. S3 validates against actual. |
| SSE-C headers | If SSE-C was used |

**Response:** `200 OK` — but **can contain error in body** (same as CopyObject).

S3 begins sending 200 before finishing assembly. If assembly fails mid-stream,
it cannot change the status code, so it sends `<Error>` XML instead.

**Success Response Body:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<CompleteMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
   <Location>https://bucket.s3.region.amazonaws.com/key</Location>
   <Bucket>bucket-name</Bucket>
   <Key>object-key</Key>
   <ETag>"composite-etag-3"</ETag>
</CompleteMultipartUploadResult>
```

**ETag Calculation (critical for implementation):**

```
ETag = "hex(MD5(MD5_binary(part1) || MD5_binary(part2) || ... || MD5_binary(partN)))-N"
```

Step-by-step:

1. For each part (in part-number order), compute MD5 → 16 raw bytes.
2. Concatenate all part MD5 hashes → N * 16 bytes buffer.
3. MD5 hash the concatenated buffer → 16 bytes.
4. Hex-encode → 32-char string.
5. Append `-N` where N = number of parts in the completion request.

```python
import hashlib

def compute_multipart_etag(part_data_list):
    md5_digests = b""
    for part_data in part_data_list:
        md5_digests += hashlib.md5(part_data).digest()  # 16 raw bytes
    composite = hashlib.md5(md5_digests).hexdigest()
    return f'"{composite}-{len(part_data_list)}"'
```

The `-N` suffix distinguishes multipart ETags from single-part. N is the
count of parts listed in the completion request, NOT total parts uploaded.

**Error Codes:**

| Code | Status | Cause |
|------|--------|-------|
| `EntityTooSmall` | 400 | Non-last part < 5 MB |
| `InvalidPart` | 400 | Part not found or ETag mismatch |
| `InvalidPartOrder` | 400 | Parts not in ascending order |
| `NoSuchUpload` | 404 | Upload already completed/aborted |
| `PreconditionFailed` | 412 | Conditional header not met |

**"Last part" determination:** The part with the highest PartNumber in the
request is the "last part" (exempt from 5 MB minimum). Determined by position,
not upload order.

**Behavior Rules:**

1. Part list must be sorted ascending by PartNumber.
2. No duplicate PartNumbers.
3. ETags must match exactly.
4. Processing can take minutes for large objects. S3 sends periodic whitespace
   to prevent client/proxy timeouts.
5. Completing same upload twice → `NoSuchUpload` on second call.

---

### 4.5 AbortMultipartUpload

**Request:** `DELETE /{bucket}/{key}?uploadId={id}`

**Response:** `204 No Content`. Parts freed asynchronously.

**Error Codes:**

| Code | Status | Cause |
|------|--------|-------|
| `NoSuchUpload` | 404 | Already completed, aborted, or never existed |

**Behavior Rules:**

1. After abort, upload ID becomes invalid.
2. **Race with in-progress uploads:** Parts being uploaded concurrently may
   succeed after abort, becoming orphaned (still consuming storage).
3. May need to call abort again or use lifecycle rules to clean up orphans.
4. Idempotent: aborting already-aborted upload → `NoSuchUpload` (404).

---

### 4.6 ListParts

**Request:** `GET /{bucket}/{key}?uploadId={id}`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uploadId` | string | (required) | Upload ID |
| `max-parts` | integer | 1000 | Max per response (cap: 1000) |
| `part-number-marker` | integer | - | List parts with PartNumber **strictly greater** than this (exclusive) |

**Response:** `200 OK`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ListPartsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
   <Bucket>bucket-name</Bucket>
   <Key>object-key</Key>
   <UploadId>upload-id</UploadId>
   <PartNumberMarker>0</PartNumberMarker>
   <NextPartNumberMarker>2</NextPartNumberMarker>
   <MaxParts>1000</MaxParts>
   <IsTruncated>false</IsTruncated>
   <Part>
      <PartNumber>1</PartNumber>
      <LastModified>2010-11-10T20:48:34.000Z</LastModified>
      <ETag>"7778aef83f66abc1fa1e8477f296d394"</ETag>
      <Size>10485760</Size>
   </Part>
   <Initiator>
      <ID>arn:aws:iam::123456789012:user/name</ID>
      <DisplayName>name</DisplayName>
   </Initiator>
   <Owner>
      <ID>canonical-id</ID>
      <DisplayName>name</DisplayName>
   </Owner>
   <StorageClass>STANDARD</StorageClass>
</ListPartsResult>
```

**Pagination:**

- `part-number-marker` is exclusive: only parts > marker returned.
- When `IsTruncated` is `true`, use `NextPartNumberMarker` as next marker.
- Hard max 1000 per response.
- Parts in ascending PartNumber order.
- Overwritten parts show only latest upload for that number.

---

### 4.7 ListMultipartUploads

**Request:** `GET /{bucket}?uploads`

Bucket-level operation (no object key in path).

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `delimiter` | string | - | Group keys (max 1 char) |
| `encoding-type` | string | - | `url` |
| `key-marker` | string | - | List uploads with keys after this value |
| `upload-id-marker` | string | - | With `key-marker`, list uploads with ID after this. Ignored without `key-marker`. |
| `max-uploads` | integer | 1000 | Range: 1-1000 |
| `prefix` | string | - | Filter by key prefix |

**Response:** `200 OK`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ListMultipartUploadsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
   <Bucket>bucket-name</Bucket>
   <KeyMarker></KeyMarker>
   <UploadIdMarker></UploadIdMarker>
   <NextKeyMarker>key</NextKeyMarker>
   <NextUploadIdMarker>id</NextUploadIdMarker>
   <MaxUploads>1000</MaxUploads>
   <IsTruncated>false</IsTruncated>
   <Upload>
      <Key>object-key</Key>
      <UploadId>upload-id</UploadId>
      <Initiator>
         <ID>arn</ID>
         <DisplayName>name</DisplayName>
      </Initiator>
      <Owner>
         <ID>canonical-id</ID>
         <DisplayName>name</DisplayName>
      </Owner>
      <StorageClass>STANDARD</StorageClass>
      <Initiated>2010-11-10T20:48:34.000Z</Initiated>
   </Upload>
   <CommonPrefixes>
      <Prefix>prefix/</Prefix>
   </CommonPrefixes>
</ListMultipartUploadsResult>
```

**Sorting:** Primary: key ascending lexicographic. Secondary: initiation
time ascending within same key.

**Pagination:**

- When `IsTruncated` = true, use both `NextKeyMarker` AND `NextUploadIdMarker`.
- `upload-id-marker` ignored without `key-marker`.
- Without `upload-id-marker`, only uploads with keys > `key-marker` returned.
- With both, uploads for exact `key-marker` key included only if upload ID >
  `upload-id-marker`.
- Max 1000 per response.

---

## 5. List Operations

### 5.1 ListObjectsV1

**Status:** Deprecated by AWS. V2 recommended for new applications, but V1
widely used by existing clients and must be supported.

**Request:** `GET /{bucket}` (no `list-type` parameter)

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prefix` | string | - | Only return keys beginning with this string |
| `delimiter` | string | - | Character to group keys into CommonPrefixes |
| `marker` | string | - | Start after this key (exclusive). For pagination. |
| `max-keys` | integer | 1000 | Maximum keys to return (0-1000) |
| `encoding-type` | string | - | `url` — percent-encode keys in response |

**Request Headers:**

| Header | Description |
|--------|-------------|
| `x-amz-request-payer` | `requester` |
| `x-amz-expected-bucket-owner` | Account ID |
| `x-amz-optional-object-attributes` | `RestoreStatus` |

**Response:** `200 OK`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>bucket-name</Name>
  <Prefix></Prefix>
  <Marker></Marker>
  <MaxKeys>1000</MaxKeys>
  <IsTruncated>false</IsTruncated>
  <NextMarker>last-key</NextMarker>
  <Delimiter>/</Delimiter>
  <EncodingType>url</EncodingType>
  <Contents>
    <Key>object-key</Key>
    <LastModified>2009-10-12T17:50:30.000Z</LastModified>
    <ETag>"fba9dede5f27731c9771645a39863328"</ETag>
    <Size>434234</Size>
    <StorageClass>STANDARD</StorageClass>
    <Owner>
      <ID>canonical-user-id</ID>
      <DisplayName>display-name</DisplayName>
    </Owner>
  </Contents>
  <CommonPrefixes>
    <Prefix>photos/</Prefix>
  </CommonPrefixes>
</ListBucketResult>
```

| Element | Presence | Description |
|---------|----------|-------------|
| `Name` | Always | Bucket name |
| `Prefix` | Always | Echo (empty string if not specified) |
| `Marker` | Always | Echo (empty string if not specified) |
| `MaxKeys` | Always | Echo of requested max-keys |
| `IsTruncated` | Always | `true` if more results exist |
| `NextMarker` | Conditional | **Only when delimiter IS set AND IsTruncated=true** |
| `Delimiter` | Conditional | Only if delimiter was specified |
| `EncodingType` | Conditional | Only if encoding-type=url requested |
| `Contents` | 0..N | One per matching object |
| `Contents/Key` | Always in Contents | Object key name |
| `Contents/LastModified` | Always in Contents | ISO 8601 with ms |
| `Contents/ETag` | Always in Contents | Quoted entity tag |
| `Contents/Size` | Always in Contents | Size in bytes |
| `Contents/StorageClass` | Always in Contents | Storage class |
| `Contents/Owner` | Always in V1 | Owner ID and DisplayName |
| `CommonPrefixes` | 0..N | One per grouped prefix |
| `CommonPrefixes/Prefix` | Always in CommonPrefixes | The grouped prefix string |

**V1 Pagination Rules:**

1. When delimiter IS set AND truncated: Response includes `NextMarker`. Use
   as next `marker`.
2. When delimiter NOT set (or not truncated): `NextMarker` **absent**. Client
   must use the `Key` of the **last** `Contents` element as next `marker`.
3. Results in lexicographic (UTF-8 byte) order by key.
4. Both `Contents` and `CommonPrefixes` count against `max-keys`.
5. `CommonPrefixes` only included if lexicographically greater than `marker`.

**Error Codes:**

| Code | Status | Cause |
|------|--------|-------|
| `NoSuchBucket` | 404 | Bucket does not exist |
| `AccessDenied` | 403 | Insufficient permissions |

**Permission:** `s3:ListBucket`.

---

### 5.2 ListObjectsV2

**Request:** `GET /{bucket}?list-type=2`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `list-type` | string | (required) | Must be `2` |
| `prefix` | string | - | Filter by key prefix |
| `delimiter` | string | - | Group keys into CommonPrefixes |
| `start-after` | string | - | Start after this key (first page only; ignored when `continuation-token` present) |
| `continuation-token` | string | - | Opaque pagination token from previous response |
| `max-keys` | integer | 1000 | Max keys (0-1000) |
| `encoding-type` | string | - | `url` |
| `fetch-owner` | boolean | false | Include `Owner` in Contents |

**Response:** `200 OK`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>bucket-name</Name>
  <Prefix></Prefix>
  <MaxKeys>1000</MaxKeys>
  <KeyCount>2</KeyCount>
  <IsTruncated>false</IsTruncated>
  <ContinuationToken>prev-token</ContinuationToken>
  <NextContinuationToken>next-token</NextContinuationToken>
  <StartAfter>start-key</StartAfter>
  <Contents>
    <Key>object-key</Key>
    <LastModified>2009-10-12T17:50:30.000Z</LastModified>
    <ETag>"fba9dede5f27731c9771645a39863328"</ETag>
    <Size>434234</Size>
    <StorageClass>STANDARD</StorageClass>
    <Owner>
      <ID>canonical-user-id</ID>
      <DisplayName>display-name</DisplayName>
    </Owner>
  </Contents>
  <CommonPrefixes>
    <Prefix>photos/</Prefix>
  </CommonPrefixes>
</ListBucketResult>
```

| Element | Presence | Description |
|---------|----------|-------------|
| `KeyCount` | Always | Actual entries in THIS page (Contents + CommonPrefixes) |
| `ContinuationToken` | If token was sent | Echo of request token |
| `NextContinuationToken` | Only when IsTruncated=true | **Always present when truncated** |
| `StartAfter` | If start-after was sent | Echo of request value |
| `Owner` | Only if fetch-owner=true | Owner in Contents |

**V2 Improvements Over V1:**

| Aspect | V1 | V2 |
|--------|----|----|
| Pagination param | `marker` (actual key) | `continuation-token` (opaque) |
| Pagination response | `NextMarker` (only with delimiter+truncated) | `NextContinuationToken` (always when truncated) |
| Start position | `marker` | `start-after` (first page only) |
| Owner | Always included | Only if `fetch-owner=true` |
| Entry count | Not present | `KeyCount` |

**V2 Pagination Rules:**

1. `continuation-token` is **opaque**. Do not parse. Server generates; client
   passes back verbatim.
2. When `continuation-token` present, `start-after` is **ignored**.
3. `NextContinuationToken` only present when `IsTruncated=true`. Always present
   when truncated (not conditional on delimiter like V1).
4. `KeyCount` = actual entries this page, not total across all pages.
5. Results in lexicographic UTF-8 byte order.

**Error Codes and Permission:** Same as V1.

---

### 5.3 DeleteObjects (Batch)

**Request:** `POST /{bucket}?delete`

**Request Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `Content-MD5` | **Yes** (general purpose buckets) | Base64 MD5 of XML body. Mismatch → 400 BadDigest. |
| `Content-Type` | Yes | `application/xml` or `text/xml` |
| `x-amz-mfa` | Conditional | `{serial} {code}` (space-separated). Required for MFA-Delete-enabled buckets. Missing/invalid → entire request fails 403. |
| `x-amz-bypass-governance-retention` | No | `true` to delete Governance-locked objects |

**Content-MD5 Computation:**

1. Compute MD5 hash of raw XML body (UTF-8 bytes) → 16 binary bytes.
2. Base64-encode the 16 bytes.
3. Set header: `Content-MD5: <base64>`.

**Request Body:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Delete xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Quiet>false</Quiet>
  <Object>
    <Key>key1</Key>
    <VersionId>version-id</VersionId>
  </Object>
  <Object>
    <Key>key2</Key>
  </Object>
</Delete>
```

| Element | Required | Description |
|---------|----------|-------------|
| `Delete` | Root | Container with xmlns |
| `Quiet` | No | Default `false`. When `true`, `Deleted` elements omitted (only `Error` elements returned). |
| `Object` | Yes (1-1000) | Up to 1000 objects per request |
| `Object/Key` | Yes | Object key name |
| `Object/VersionId` | No | Specific version to delete |

**Response:** `200 OK` — **ALWAYS 200, even if every single object fails.**
Per-object errors are in the XML body.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<DeleteResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Deleted>
    <Key>key1</Key>
    <VersionId>version-id</VersionId>
    <DeleteMarker>true</DeleteMarker>
    <DeleteMarkerVersionId>marker-version-id</DeleteMarkerVersionId>
  </Deleted>
  <Error>
    <Key>key2</Key>
    <Code>AccessDenied</Code>
    <Message>Access Denied</Message>
    <VersionId>version-id</VersionId>
  </Error>
</DeleteResult>
```

**Quiet Mode:**

| `Quiet` | `Deleted` elements | `Error` elements |
|---------|-------------------|-----------------|
| `false` (default, verbose) | Included for every success | Included for every failure |
| `true` | Omitted entirely | Still included for every failure |

In quiet mode with all successes, response body is empty `<DeleteResult/>`.

**Versioning Behavior:**

| Scenario | Result |
|----------|--------|
| Non-versioned, key exists | Permanently removed. `<Deleted>` with `<Key>`. |
| Versioned, no VersionId | Delete marker created. `<Deleted>` with `<DeleteMarker>true</DeleteMarker>` + `<DeleteMarkerVersionId>`. |
| Versioned, with VersionId | Specific version permanently removed. |
| Deleting a delete marker | Marker removed ("undelete"). `<DeleteMarker>true</DeleteMarker>`. |
| **Key does not exist** | **Treated as success.** `<Deleted>` element returned. |

**Critical Behavior:**

1. HTTP 200 even when all objects fail. Check `<Error>` elements.
2. Deleting nonexistent keys succeeds — returns `<Deleted>`, not `<Error>`.
3. `Content-MD5` mandatory on general purpose buckets.
4. Max 1000 objects per request.
5. MFA Delete: missing/invalid token → entire request fails 403.

**Permissions:** `s3:DeleteObject` for unversioned, `s3:DeleteObjectVersion`
for versioned deletes.

---

### 5.4 ListObjectVersions

**Request:** `GET /{bucket}?versions`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prefix` | string | - | Filter by key prefix |
| `delimiter` | string | - | Group keys |
| `key-marker` | string | - | Start after this key |
| `version-id-marker` | string | - | With `key-marker`, start after this version. **Ignored without `key-marker`.** |
| `max-keys` | integer | 1000 | Max entries (0-1000) |
| `encoding-type` | string | - | `url` |

**Response:** `200 OK`

Root element: **`ListVersionsResult`** (different from `ListBucketResult`).

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>bucket-name</Name>
  <Prefix></Prefix>
  <KeyMarker></KeyMarker>
  <VersionIdMarker></VersionIdMarker>
  <NextKeyMarker>next-key</NextKeyMarker>
  <NextVersionIdMarker>next-version</NextVersionIdMarker>
  <MaxKeys>1000</MaxKeys>
  <IsTruncated>false</IsTruncated>

  <Version>
    <Key>object-key</Key>
    <VersionId>version-id</VersionId>
    <IsLatest>true</IsLatest>
    <LastModified>2009-10-12T17:50:30.000Z</LastModified>
    <ETag>"fba9dede5f27731c9771645a39863328"</ETag>
    <Size>434234</Size>
    <StorageClass>STANDARD</StorageClass>
    <Owner>
      <ID>canonical-user-id</ID>
      <DisplayName>display-name</DisplayName>
    </Owner>
  </Version>

  <DeleteMarker>
    <Key>object-key</Key>
    <VersionId>marker-version-id</VersionId>
    <IsLatest>false</IsLatest>
    <LastModified>2009-10-12T17:50:30.000Z</LastModified>
    <Owner>
      <ID>canonical-user-id</ID>
      <DisplayName>display-name</DisplayName>
    </Owner>
  </DeleteMarker>

  <CommonPrefixes>
    <Prefix>photos/</Prefix>
  </CommonPrefixes>
</ListVersionsResult>
```

**Key Differences from ListObjects:**

| Aspect | ListObjects | ListObjectVersions |
|--------|------------|-------------------|
| Root element | `ListBucketResult` | `ListVersionsResult` |
| Object entries | `Contents` | `Version` (0..N) + `DeleteMarker` (0..N) |
| Pagination | Single marker or token | Two markers: `key-marker` + `version-id-marker` |
| Version metadata | Not present | `VersionId`, `IsLatest` |
| DeleteMarker elements | Not present | Separate elements (no ETag, Size, StorageClass) |
| Owner | Conditional | Always included |
| Permission | `s3:ListBucket` | `s3:ListBucketVersions` |

**DeleteMarker elements have NO ETag, Size, or StorageClass.** They are
lightweight markers, not real objects.

**Ordering:**

1. Across keys: Lexicographic (UTF-8 byte) order by key name.
2. Within same key: Reverse chronological (most recent version first).
3. `Version` and `DeleteMarker` for same key interleaved in time-descending
   order.
4. Exactly one `Version` or `DeleteMarker` per key has `IsLatest=true`.

**Pagination:**

1. When truncated, use both `NextKeyMarker` AND `NextVersionIdMarker`.
2. `version-id-marker` without `key-marker` is ignored.
3. `key-marker` alone: starts at first version of next key.
4. Both markers: starts after specific version.

---

### 5.5 Delimiter/Prefix Virtual Directory Logic

This algorithm is identical across ListObjectsV1, V2, ListObjectVersions, and
ListMultipartUploads. It creates the illusion of hierarchical directories in
S3's flat key-value namespace.

**Algorithm:**

Given prefix P and delimiter D:

1. Start with all keys beginning with P.
2. For each key, find the **first** occurrence of D **after** P.
3. If D found: extract substring from key start through D (inclusive) →
   CommonPrefixes (deduplicated; each unique prefix = 1 entry against max-keys).
4. If D not found: key is a direct result → Contents.

**Worked Examples:**

Keys in bucket:
```
sample.jpg
photos/2006/January/pic.jpg
photos/2006/February/pic2.jpg
photos/2006/February/pic3.jpg
```

**Example A:** `prefix=""`, `delimiter="/"`

| Key | First `/` | Result |
|-----|-----------|--------|
| `sample.jpg` | Not found | Contents: `sample.jpg` |
| `photos/2006/January/pic.jpg` | Position 6 | CommonPrefixes: `photos/` |
| `photos/2006/February/pic2.jpg` | Position 6 | CommonPrefixes: `photos/` (dedup) |

Result: Contents=`sample.jpg`, CommonPrefixes=`photos/`. Count: 2.

**Example B:** `prefix="photos/2006/"`, `delimiter="/"`

| Key (after prefix match) | Remainder | First `/` in remainder | Result |
|--------------------------|-----------|----------------------|--------|
| `photos/2006/January/pic.jpg` | `January/pic.jpg` | Position 7 | CommonPrefixes: `photos/2006/January/` |
| `photos/2006/February/pic2.jpg` | `February/pic2.jpg` | Position 8 | CommonPrefixes: `photos/2006/February/` |
| `photos/2006/February/pic3.jpg` | `February/pic3.jpg` | Position 8 | CommonPrefixes: `photos/2006/February/` (dedup) |

Result: Contents=(none), CommonPrefixes=`photos/2006/January/`, `photos/2006/February/`. Count: 2.

**Example C:** `prefix="photos/2006/February/"`, `delimiter="/"`

All matching keys' remainders (`pic2.jpg`, `pic3.jpg`) contain no `/`.

Result: Contents=`photos/2006/February/pic2.jpg`, `photos/2006/February/pic3.jpg`. Count: 2.

**Edge Cases:**

- Prefix only, no delimiter: All matching keys returned as Contents. No
  CommonPrefixes. Flat listing.
- Delimiter only, no prefix: Algorithm operates on entire key. `delimiter=/`
  gives "top-level directories."
- CommonPrefixes interact with pagination markers — only included if
  lexicographically after the marker value.

### 5.6 encoding-type=url Behavior

When `encoding-type=url` specified, certain XML element values are
percent-encoded using UTF-8.

**Why:** XML 1.0 cannot represent certain byte values (0x00-0x08, 0x0B, 0x0C,
0x0E-0x1F). S3 allows any byte sequence as a key name. URL-encoding avoids
unparseable XML.

**Elements that ARE encoded:** `Key`, `Prefix`, `Delimiter`, `Marker`,
`NextMarker`, `StartAfter`, `KeyMarker`, `NextKeyMarker`.

**Elements NOT encoded:** `ContinuationToken`, `NextContinuationToken`,
`VersionId`, `ETag`, `StorageClass`, `Owner/*`, `Name`.

**Response signal:** `<EncodingType>url</EncodingType>` included to signal
client that decoding is needed.

### 5.7 Implementation Notes for List Operations

1. **V1 NextMarker quirk:** Only include `NextMarker` when delimiter IS set
   AND truncated. Strict S3 omits it without delimiter.
2. **V2 continuation tokens are opaque.** Can encode whatever internal state
   needed (e.g., base64 of last key).
3. **KeyCount in V2** is entries in this page, not total across all pages.
4. **Empty parameters:** When `prefix`, `marker`, etc. not provided, include
   in response with empty string values (e.g., `<Prefix></Prefix>`).
5. **ETag format:** Always quoted in Contents/Version elements.
6. **LastModified format:** ISO 8601 with millisecond precision: `2009-10-12T17:50:30.000Z`.
7. **StorageClass values:** `STANDARD`, `STANDARD_IA`, `ONEZONE_IA`, `GLACIER`,
   `DEEP_ARCHIVE`, `INTELLIGENT_TIERING`, `REDUCED_REDUNDANCY`, `GLACIER_IR`.
8. **Version ordering:** Within same key, newest first (reverse chronological).
