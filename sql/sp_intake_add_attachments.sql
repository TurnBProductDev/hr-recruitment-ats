-- =============================================================================
-- sp_intake_add_attachments
-- Called by the Careers-intake Logic App (CV-Automation-Flow-Final) once per
-- email, after every attachment has been read. Files the email's non-CV
-- attachments - a cover letter, certificates, ... (CVExtractAPIView's
-- document_type 'cover_letter'/'other') - as Attachment rows on the candidate
-- that same email created, instead of as candidates of their own. See
-- sp_intake_add_candidate's @source_message_id.
--
-- @attachments_json: [{"name":"Cover Letter.pdf","url":"https://...",
--                      "document_type":"cover_letter","email":"a@b.com"}, ...]
-- (email = whatever the model read off the document itself, often null).
--
-- Which candidate gets each document: the one from this email with the same
-- email address, when the document states one; otherwise the only candidate
-- the email created. An email that created no candidate (nothing but a
-- brochure, say), or several with no way to tell whose this is, attaches
-- nothing - the file itself is still in blob storage/SharePoint, as before.
--
-- Re-runnable: CREATE OR ALTER. Safe to re-run for the same email - a
-- document already attached to that candidate (same url) is skipped.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.sp_intake_add_attachments
    @source_message_id nvarchar(255),
    @attachments_json  nvarchar(max)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @now datetimeoffset(7) = SYSDATETIMEOFFSET();

    DECLARE @only_id bigint = NULL;
    IF (SELECT COUNT(*) FROM dbo.candidates_candidate WHERE source_message_id = @source_message_id) = 1
        SET @only_id = (SELECT id FROM dbo.candidates_candidate WHERE source_message_id = @source_message_id);

    -- file has no database-level default (Django's blank FileField is
    -- app-side only) - list it explicitly as '' for a link-only attachment.
    INSERT INTO dbo.candidates_attachment (candidate_id, [file], url, label, uploaded_by_id, uploaded_at)
    SELECT t.candidate_id, '', LEFT(t.url, 1000),
           LEFT(CASE t.document_type WHEN 'cover_letter' THEN N'Cover letter' ELSE N'Other document' END
                + N' - ' + ISNULL(t.name, N'attachment'), 255),
           NULL, @now
    FROM (
        SELECT x.name, x.url, x.document_type,
               COALESCE(
                   (SELECT TOP 1 c.id FROM dbo.candidates_candidate c
                    WHERE c.source_message_id = @source_message_id
                      AND c.email = LOWER(LTRIM(RTRIM(x.email)))
                    ORDER BY c.id),
                   @only_id) AS candidate_id
        FROM OPENJSON(@attachments_json) WITH (
            name          nvarchar(255)  '$.name',
            url           nvarchar(1000) '$.url',
            document_type nvarchar(20)   '$.document_type',
            email         nvarchar(254)  '$.email'
        ) x
        WHERE x.url IS NOT NULL AND LTRIM(RTRIM(x.url)) <> ''
    ) t
    WHERE t.candidate_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dbo.candidates_attachment a
                      WHERE a.candidate_id = t.candidate_id AND a.url = t.url);

    SELECT @@ROWCOUNT AS attached_count;
END
