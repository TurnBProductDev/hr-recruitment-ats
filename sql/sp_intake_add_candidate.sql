-- =============================================================================
-- sp_intake_add_candidate
-- Called by the Careers-intake Logic App. Inserts one candidate into the ATS
-- exactly the way the web app does:
--   * generates a unique candidate_code (CAND-XXXXXXXXXX)
--   * resolves the vacancy from the role text (falls back to General Application)
--   * flags duplicates (email already seen) and blacklisted emails
--   * upserts the email registry
--   * writes the initial "Applied" status-history row (status = OPEN)
--   * writes structured Education/Experience rows, same shape Django's
--     candidates/services.py::create_from_parsed_cv builds
-- The new candidate appears in the Open Applications tab.
--
-- Backward compatible: every parameter added after @cv_summary is OPTIONAL
-- (defaults to NULL) and named, not positional - the current, unmodified
-- "CV-Automation-Flow" Logic App action keeps calling this with only the
-- original parameters and keeps working exactly as before, writing NULL into
-- the new columns. Nothing here breaks it. The new parameters exist for the
-- CV-Automation-Flow-Final rebuild (see logic_apps/README.md), which will be
-- promoted to live only after it's been verified end-to-end.
--
-- Re-runnable: CREATE OR ALTER, so a failed deploy can never leave the live
-- procedure dropped (the Logic App would start erroring on every application).
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.sp_intake_add_candidate
    @full_name    nvarchar(255),
    @email        nvarchar(254),
    @phone        nvarchar(20)   = NULL,
    @role_applied nvarchar(255)  = NULL,
    @education    nvarchar(255)  = NULL,
    @cv_link      nvarchar(1000) = NULL,
    @source       nvarchar(255)  = NULL,
    @mail_date    date           = NULL,
    @cv_summary   nvarchar(max)  = NULL,
    -- Added for the cv_extraction.py-based rebuild (CV-Automation-Flow-Final) -
    -- every one of these is optional and defaults to NULL, see note above.
    @linkedin                nvarchar(200)  = NULL,
    @portfolio_url           nvarchar(200)  = NULL,
    @current_location        nvarchar(255)  = NULL,
    @dob                     date           = NULL,
    @last_role               nvarchar(255)  = NULL,
    @last_company            nvarchar(255)  = NULL,
    @total_experience_years  decimal(5,1)   = NULL,
    @skills                  nvarchar(max)  = NULL,
    @notice_period           nvarchar(100)  = NULL,
    @expected_salary         nvarchar(100)  = NULL,
    @current_salary          nvarchar(100)  = NULL,
    -- JSON array: [{"company_name":"...","designation":"...",
    -- "start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","skills":"..."}, ...]
    -- Mirrors CandidateExperience the same way @education already mirrors
    -- CandidateEducation. A malformed entry (bad date, missing company) is
    -- skipped, not a hard failure - never blocks the candidate being created.
    @experience_json         nvarchar(max)  = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @email_norm nvarchar(254) = LOWER(LTRIM(RTRIM(@email)));

    -- Canonicalise the source so 'LinkedIn'/'Linked In' etc. never split the
    -- dashboard. Mirror of candidates.models.canonical_source — keep in sync.
    DECLARE @src nvarchar(255) = LTRIM(RTRIM(ISNULL(@source, 'Careers')));
    DECLARE @src_key nvarchar(255) = REPLACE(LOWER(@src), ' ', '');
    SET @src =
        CASE
            WHEN @src_key = 'linkedin' THEN 'Linked In'
            WHEN @src_key IN ('careers', 'careersportal') THEN 'Careers'
            WHEN @src_key IN ('referral', 'employeereference') THEN 'Referral'
            WHEN @src_key = 'naukri' THEN 'Naukri'
            WHEN @src_key = 'agency' THEN 'Agency'
            ELSE @src
        END;
    DECLARE @now datetimeoffset(7) = SYSDATETIMEOFFSET();
    DECLARE @created datetimeoffset(7) =
        CASE WHEN @mail_date IS NULL THEN @now
             ELSE TODATETIMEOFFSET(CAST(@mail_date AS datetime2), DATEPART(TZOFFSET, @now)) END;

    -- Resolve vacancy: exact title match, else General Application, else NULL
    DECLARE @job_id bigint = (SELECT TOP 1 id FROM dbo.jobs_job
        WHERE LOWER(title) = LOWER(LTRIM(RTRIM(@role_applied))) ORDER BY id);
    IF @job_id IS NULL
        SET @job_id = (SELECT TOP 1 id FROM dbo.jobs_job WHERE title = 'General Application' ORDER BY id);

    -- Duplicate + blacklist detection
    DECLARE @is_dup bit = CASE WHEN EXISTS (
        SELECT 1 FROM dbo.candidates_emailregistry WHERE LOWER(email) = @email_norm) THEN 1 ELSE 0 END;
    DECLARE @is_black bit = CASE WHEN EXISTS (
        SELECT 1 FROM dbo.candidates_blacklist b
        JOIN dbo.candidates_candidate c ON c.id = b.candidate_id
        WHERE LOWER(c.email) = @email_norm) THEN 1 ELSE 0 END;
    DECLARE @status nvarchar(30) = CASE WHEN @is_black = 1 THEN 'BLACKLISTED' ELSE 'OPEN' END;

    -- Candidate code: ID + current year + 5-digit sequence (resets each year), e.g. ID202600001
    DECLARE @prefix nvarchar(10) = 'ID' + CAST(YEAR(@now) AS nvarchar(4));
    DECLARE @next int = ISNULL((
        SELECT MAX(CAST(SUBSTRING(candidate_code, LEN(@prefix) + 1, 5) AS int))
        FROM dbo.candidates_candidate
        WHERE candidate_code LIKE @prefix + '[0-9][0-9][0-9][0-9][0-9]'), 0) + 1;
    DECLARE @code nvarchar(20) = @prefix + RIGHT('00000' + CAST(@next AS nvarchar(5)), 5);

    -- Keep the position the applicant actually asked for. @job_id above may have
    -- fallen back to General Application; role_applied preserves the original text
    -- so the General Applications page can still group them by what they wanted.
    DECLARE @role nvarchar(255) = NULLIF(LTRIM(RTRIM(ISNULL(@role_applied, ''))), '');

    -- Best-effort parse of "Degree - College - Year" - done up front (unlike
    -- the old version, which parsed this after the main insert) so the split
    -- institution can go straight onto the candidate row too, matching
    -- candidates/cv_extraction.py + services.py's own qualification/
    -- institution split.
    DECLARE @edu_qual nvarchar(255) = NULL, @edu_inst nvarchar(255) = NULL, @edu_yr int = NULL;
    IF @education IS NOT NULL AND LEN(LTRIM(RTRIM(@education))) > 0
    BEGIN
        DECLARE @edu nvarchar(255) = LTRIM(RTRIM(@education));
        DECLARE @p1 int = CHARINDEX(' - ', @edu);
        IF @p1 > 0
        BEGIN
            SET @edu_qual = LTRIM(RTRIM(LEFT(@edu, @p1 - 1)));
            SET @edu_inst = LTRIM(RTRIM(SUBSTRING(@edu, @p1 + 3, 255)));
            -- strip a trailing " - YYYY" into the year
            IF LEN(@edu_inst) >= 7 AND SUBSTRING(@edu_inst, LEN(@edu_inst) - 6, 3) = ' - '
               AND RIGHT(@edu_inst, 4) LIKE '[12][0-9][0-9][0-9]'
            BEGIN
                SET @edu_yr   = CAST(RIGHT(@edu_inst, 4) AS int);
                SET @edu_inst = LTRIM(RTRIM(LEFT(@edu_inst, LEN(@edu_inst) - 7)));
            END
        END
        ELSE SET @edu_qual = @edu;
    END

    -- match_state has no database-level default (Django's default='PENDING' on
    -- the model is app-side only) - list it explicitly or a NOT NULL candidates_candidate
    -- column omitted here fails the insert. Keep this in sync with any future
    -- NOT NULL Candidate field that has only a Django-side default.
    INSERT INTO dbo.candidates_candidate
        (candidate_code, full_name, email, phone, qualification, institution, resume_url,
         source, status, is_duplicate, is_blacklisted, is_on_hold, hold_from_status,
         created_at, updated_at, job_id, cv_summary, role_applied, match_state,
         linkedin, portfolio_url, current_location, dob, last_role, last_company,
         total_experience_years, skills, notice_period, expected_salary, current_salary)
    VALUES
        (@code, @full_name, @email_norm, @phone, @education, NULLIF(@edu_inst, ''), @cv_link,
         @src, @status, @is_dup, @is_black, 0, '',
         @created, @now, @job_id, @cv_summary, @role, 'PENDING',
         @linkedin, @portfolio_url, @current_location, @dob, @last_role, @last_company,
         @total_experience_years, @skills, @notice_period, @expected_salary, @current_salary);
    DECLARE @cid bigint = SCOPE_IDENTITY();

    IF @is_dup = 1
        UPDATE dbo.candidates_emailregistry
        SET application_count = application_count + 1, last_applied_at = @now
        WHERE LOWER(email) = @email_norm;
    ELSE
        INSERT INTO dbo.candidates_emailregistry (email, application_count, last_applied_at, first_candidate_id)
        VALUES (@email_norm, 1, @now, @cid);

    INSERT INTO dbo.candidates_candidatestatushistory
        (old_status, new_status, remarks, changed_at, candidate_id, performed_by)
    VALUES ('', @status, 'Applied via careers intake (Logic App)', @created, @cid, 'Careers Intake');

    -- Structured Education record, from the same split computed above.
    IF @edu_qual IS NOT NULL
        INSERT INTO dbo.candidates_candidateeducation
            (candidate_id, qualification, institution, year_completed)
        VALUES (@cid, LEFT(NULLIF(@edu_qual, ''), 255), NULLIF(@edu_inst, ''), @edu_yr);

    -- Structured Experience records - see @experience_json's declaration above.
    -- TRY_CONVERT never raises on a bad date, it just yields NULL, so one
    -- malformed entry doesn't stop the rest of the row (or the candidate)
    -- from being created.
    IF @experience_json IS NOT NULL AND LEN(LTRIM(RTRIM(@experience_json))) > 0
    BEGIN
        INSERT INTO dbo.candidates_candidateexperience
            (candidate_id, company_name, designation, start_date, end_date, skills)
        SELECT @cid, LEFT(x.company_name, 255), LEFT(x.designation, 255),
               TRY_CONVERT(date, x.start_date), TRY_CONVERT(date, x.end_date), LEFT(x.skills, 500)
        FROM OPENJSON(@experience_json)
        WITH (
            company_name nvarchar(255) '$.company_name',
            designation  nvarchar(255) '$.designation',
            start_date   nvarchar(20)  '$.start_date',
            end_date     nvarchar(20)  '$.end_date',
            skills       nvarchar(500) '$.skills'
        ) AS x
        WHERE x.company_name IS NOT NULL AND LTRIM(RTRIM(x.company_name)) <> '';
    END

    SELECT @cid AS candidate_id, @code AS candidate_code, @status AS [status],
           @is_dup AS is_duplicate, @job_id AS job_id;
END
