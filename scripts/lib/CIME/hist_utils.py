"""
Functions for actions pertaining to history files.
"""
from CIME.XML.standard_module_setup import *
from CIME.test_status import TEST_NO_BASELINES_COMMENT, TEST_STATUS_FILENAME
from CIME.utils import get_current_commit, get_timestamp, get_model, safe_copy, SharedArea, parse_test_name

import logging, os, re, filecmp
logger = logging.getLogger(__name__)

BLESS_LOG_NAME = "bless_log"

# ------------------------------------------------------------------------
# Strings used in the comments generated by cprnc
# ------------------------------------------------------------------------

CPRNC_FIELDLISTS_DIFFER = "files differ only in their field lists"

# ------------------------------------------------------------------------
# Strings used in the comments generated by _compare_hists
# ------------------------------------------------------------------------

NO_COMPARE        = "had no compare counterpart"
NO_ORIGINAL       = "had no original counterpart"
FIELDLISTS_DIFFER = "had a different field list from"
DIFF_COMMENT      = "did NOT match"
# COMPARISON_COMMENT_OPTIONS should include all of the above: these are any of the special
# comment strings that describe the reason for a comparison failure
COMPARISON_COMMENT_OPTIONS = set([NO_COMPARE,
                                  NO_ORIGINAL,
                                  FIELDLISTS_DIFFER,
                                  DIFF_COMMENT])
# Comments that indicate a true baseline comparison failure
COMPARISON_FAILURE_COMMENT_OPTIONS = (COMPARISON_COMMENT_OPTIONS
                                      - set([NO_COMPARE, FIELDLISTS_DIFFER]))

def _iter_model_file_substrs(case):
    models = case.get_compset_components()
    models.append('cpl')
    for model in models:
        yield model

def copy_histfiles(case, suffix):
    """Copy the most recent batch of hist files in a case, adding the given suffix.

    This can allow you to temporarily "save" these files so they won't be blown
    away if you re-run the case.

    case - The case containing the files you want to save
    suffix - The string suffix you want to add to saved files, this can be used to find them later.
    """
    rundir   = case.get_value("RUNDIR")
    ref_case = case.get_value("RUN_REFCASE")
    casename = case.get_value("CASE")
    # Loop over models
    archive = case.get_env("archive")
    comments = "Copying hist files to suffix '{}'\n".format(suffix)
    num_copied = 0
    for model in _iter_model_file_substrs(case):
        comments += "  Copying hist files for model '{}'\n".format(model)
        test_hists = archive.get_latest_hist_files(casename, model, rundir, ref_case=ref_case)
        num_copied += len(test_hists)
        for test_hist in test_hists:
            test_hist = os.path.join(rundir,test_hist)
            if not test_hist.endswith('.nc'):
                logger.info("Will not compare non-netcdf file {}".format(test_hist))
                continue
            new_file = "{}.{}".format(test_hist, suffix)
            if os.path.exists(new_file):
                os.remove(new_file)

            comments += "    Copying '{}' to '{}'\n".format(test_hist, new_file)

            # Need to copy rather than move in case there are some history files
            # that will need to continue to be filled on the next phase; this
            # can be the case for a restart run.
            #
            # (If it weren't for that possibility, a move/rename would be more
            # robust here: The problem with a copy is that there can be
            # confusion after the second run as to which files were created by
            # the first run and which by the second. For example, if the second
            # run fails to output any history files, the test will still pass,
            # because the test system will think that run1's files were output
            # by run2. But we live with that downside for the sake of the reason
            # noted above.)
            safe_copy(test_hist, new_file)

    expect(num_copied > 0, "copy_histfiles failed: no hist files found in rundir '{}'".format(rundir))

    return comments

def rename_all_hist_files(case, suffix):
    """Renaming all hist files in a case, adding the given suffix.

    case - The case containing the files you want to save
    suffix - The string suffix you want to add to saved files, this can be used to find them later.
    """
    rundir   = case.get_value("RUNDIR")
    ref_case = case.get_value("RUN_REFCASE")
    # Loop over models
    archive = case.get_env("archive")
    comments = "Renaming hist files by adding suffix '{}'\n".format(suffix)
    num_renamed = 0
    for model in _iter_model_file_substrs(case):
        comments += "  Renaming hist files for model '{}'\n".format(model)

        if model == 'cpl':
            mname = 'drv'
        else:
            mname = model
        test_hists = archive.get_all_hist_files(mname, rundir, ref_case=ref_case)
        num_renamed += len(test_hists)
        for test_hist in test_hists:
            test_hist = os.path.join(rundir, test_hist)
            new_file = "{}.{}".format(test_hist, suffix)
            if os.path.exists(new_file):
                os.remove(new_file)

            comments += "    Renaming '{}' to '{}'\n".format(test_hist, new_file)

            os.rename(test_hist, new_file)

    expect(num_renamed > 0, "renaming failed: no hist files found in rundir '{}'".format(rundir))

    return comments

def _hists_match(model, hists1, hists2, suffix1="", suffix2=""):
    """
    return (num in set 1 but not 2 , num in set 2 but not 1, matchups)

    >>> hists1 = ['FOO.G.cpl.h1.nc', 'FOO.G.cpl.h2.nc', 'FOO.G.cpl.h3.nc']
    >>> hists2 = ['cpl.h2.nc', 'cpl.h3.nc', 'cpl.h4.nc']
    >>> _hists_match('cpl', hists1, hists2)
    (['FOO.G.cpl.h1.nc'], ['cpl.h4.nc'], [('FOO.G.cpl.h2.nc', 'cpl.h2.nc'), ('FOO.G.cpl.h3.nc', 'cpl.h3.nc')])
    >>> hists1 = ['FOO.G.cpl.h1.nc.SUF1', 'FOO.G.cpl.h2.nc.SUF1', 'FOO.G.cpl.h3.nc.SUF1']
    >>> hists2 = ['cpl.h2.nc.SUF2', 'cpl.h3.nc.SUF2', 'cpl.h4.nc.SUF2']
    >>> _hists_match('cpl', hists1, hists2, 'SUF1', 'SUF2')
    (['FOO.G.cpl.h1.nc.SUF1'], ['cpl.h4.nc.SUF2'], [('FOO.G.cpl.h2.nc.SUF1', 'cpl.h2.nc.SUF2'), ('FOO.G.cpl.h3.nc.SUF1', 'cpl.h3.nc.SUF2')])
    >>> hists1 = ['cam.h0.1850-01-08-00000.nc']
    >>> hists2 = ['cam_0001.h0.1850-01-08-00000.nc','cam_0002.h0.1850-01-08-00000.nc']
    >>> _hists_match('cam', hists1, hists2, '', '')
    ([], [], [('cam.h0.1850-01-08-00000.nc', 'cam_0001.h0.1850-01-08-00000.nc'), ('cam.h0.1850-01-08-00000.nc', 'cam_0002.h0.1850-01-08-00000.nc')])
    >>> hists1 = ['cam_0001.h0.1850-01-08-00000.nc.base','cam_0002.h0.1850-01-08-00000.nc.base']
    >>> hists2 = ['cam_0001.h0.1850-01-08-00000.nc.rest','cam_0002.h0.1850-01-08-00000.nc.rest']
    >>> _hists_match('cam', hists1, hists2, 'base', 'rest')
    ([], [], [('cam_0001.h0.1850-01-08-00000.nc.base', 'cam_0001.h0.1850-01-08-00000.nc.rest'), ('cam_0002.h0.1850-01-08-00000.nc.base', 'cam_0002.h0.1850-01-08-00000.nc.rest')])
    """
    normalized1, normalized2 = [], []
    multi_normalized1, multi_normalized2 = [], []
    multiinst = False

    for hists, suffix, normalized, multi_normalized in [(hists1, suffix1, normalized1, multi_normalized1), (hists2, suffix2, normalized2, multi_normalized2)]:
        for hist in hists:
            hist_basename = os.path.basename(hist)
            offset = hist_basename.rfind(model)
            expect(offset >= 0,"ERROR: cant find model name {} in {}".format(model, hist_basename))
            normalized_name = os.path.basename(hist_basename[offset:])
            if suffix != "":
                expect(normalized_name.endswith(suffix), "How did '{}' not have suffix '{}'".format(hist, suffix))
                normalized_name = normalized_name[:len(normalized_name) - len(suffix) - 1]

            m = re.search("(.+)_[0-9]{4}(.+.nc)",normalized_name)
            if m is not None:
                multiinst = True
                multi_normalized.append(m.group(1)+m.group(2))

            normalized.append(normalized_name)

    set_of_1_not_2 = set(normalized1) - set(normalized2)
    set_of_2_not_1 = set(normalized2) - set(normalized1)

    one_not_two = sorted([hists1[normalized1.index(item)] for item in set_of_1_not_2])
    two_not_one = sorted([hists2[normalized2.index(item)] for item in set_of_2_not_1])

    both = set(normalized1) & set(normalized2)

    match_ups = sorted([ (hists1[normalized1.index(item)], hists2[normalized2.index(item)]) for item in both])

    # Special case - comparing multiinstance to single instance files

    if multi_normalized1 != multi_normalized2:
        # in this case hists1 contains multiinstance hists2 does not
        if set(multi_normalized1) == set(normalized2):
            for idx, norm_hist1 in enumerate(multi_normalized1):
                for idx1, hist2 in enumerate(hists2):
                    norm_hist2 = normalized2[idx1]
                    if norm_hist1 == norm_hist2:
                        match_ups.append((hists1[idx], hist2))
                        if hist2 in two_not_one:
                            two_not_one.remove(hist2)
                        if hists1[idx] in one_not_two:
                            one_not_two.remove(hists1[idx])
        # in this case hists2 contains multiinstance hists1 does not
        if set(multi_normalized2) == set(normalized1):
            for idx, norm_hist2 in enumerate(multi_normalized2):
                for idx1, hist1 in enumerate(hists1):
                    norm_hist1 = normalized1[idx1]
                    if norm_hist2 == norm_hist1:
                        match_ups.append((hist1, hists2[idx]))
                        if hist1 in one_not_two:
                            one_not_two.remove(hist1)
                        if hists2[idx] in two_not_one:
                            two_not_one.remove(hists2[idx])

    if not multiinst:
        expect(len(match_ups) + len(set_of_1_not_2) == len(hists1), "Programming error1")
        expect(len(match_ups) + len(set_of_2_not_1) == len(hists2), "Programming error2")

    return one_not_two, two_not_one, match_ups

def _compare_hists(case, from_dir1, from_dir2, suffix1="", suffix2="", outfile_suffix="",
                   ignore_fieldlist_diffs=False):
    if from_dir1 == from_dir2:
        expect(suffix1 != suffix2, "Comparing files to themselves?")

    casename = case.get_value("CASE")
    testcase = case.get_value("TESTCASE")
    casedir = case.get_value("CASEROOT")
    all_success = True
    num_compared = 0
    comments = "Comparing hists for case '{}' dir1='{}', suffix1='{}',  dir2='{}' suffix2='{}'\n".format(casename, from_dir1, suffix1, from_dir2, suffix2)
    multiinst_driver_compare = False
    archive = case.get_env('archive')
    ref_case = case.get_value("RUN_REFCASE")
    for model in _iter_model_file_substrs(case):
        if model == 'cpl' and suffix2 == 'multiinst':
            multiinst_driver_compare = True
        comments += "  comparing model '{}'\n".format(model)
        hists1 = archive.get_latest_hist_files(casename, model, from_dir1, suffix=suffix1, ref_case=ref_case)
        hists2 = archive.get_latest_hist_files(casename, model, from_dir2, suffix=suffix2, ref_case=ref_case)

        if len(hists1) == 0 and len(hists2) == 0:
            comments += "    no hist files found for model {}\n".format(model)
            continue

        one_not_two, two_not_one, match_ups = _hists_match(model, hists1, hists2, suffix1, suffix2)
        for item in one_not_two:
            if 'initial' in item:
                continue
            comments += "    File '{}' {} in '{}' with suffix '{}'\n".format(item, NO_COMPARE, from_dir2, suffix2)
            all_success = False

        for item in two_not_one:
            if 'initial' in item:
                continue
            comments += "    File '{}' {} in '{}' with suffix '{}'\n".format(item, NO_ORIGINAL, from_dir1, suffix1)
            all_success = False

        num_compared += len(match_ups)

        for hist1, hist2 in match_ups:
            if not '.nc' in hist1:
                logger.info("Ignoring non-netcdf file {}".format(hist1))
                continue
            success, cprnc_log_file, cprnc_comment = cprnc(model, os.path.join(from_dir1,hist1),
                                                           os.path.join(from_dir2,hist2), case, from_dir1,
                                                           multiinst_driver_compare=multiinst_driver_compare,
                                                           outfile_suffix=outfile_suffix,
                                                           ignore_fieldlist_diffs=ignore_fieldlist_diffs)
            if success:
                comments += "    {} matched {}\n".format(hist1, hist2)
            else:
                if cprnc_comment == CPRNC_FIELDLISTS_DIFFER:
                    comments += "    {} {} {}\n".format(hist1, FIELDLISTS_DIFFER, hist2)
                else:
                    comments += "    {} {} {}\n".format(hist1, DIFF_COMMENT, hist2)
                comments += "    cat " + cprnc_log_file + "\n"
                expected_log_file = os.path.join(casedir, os.path.basename(cprnc_log_file))
                if not (os.path.exists(expected_log_file) and filecmp.cmp(cprnc_log_file, expected_log_file)):
                    try:
                        safe_copy(cprnc_log_file, casedir)
                    except (OSError, IOError) as _:
                        logger.warning("Could not copy {} to {}".format(cprnc_log_file, casedir))

                all_success = False
    # PFS test may not have any history files to compare.
    if num_compared == 0 and testcase != "PFS":
        all_success = False
        comments += "Did not compare any hist files! Missing baselines?\n"

    comments += "PASS" if all_success else "FAIL"

    return all_success, comments

def compare_test(case, suffix1, suffix2, ignore_fieldlist_diffs=False):
    """
    Compares two sets of component history files in the testcase directory

    case - The case containing the hist files to compare
    suffix1 - The suffix that identifies the first batch of hist files
    suffix1 - The suffix that identifies the second batch of hist files
    ignore_fieldlist_diffs (bool): If True, then: If the two cases differ only in their
        field lists (i.e., all shared fields are bit-for-bit, but one case has some
        diagnostic fields that are missing from the other case), treat the two cases as
        identical.

    returns (SUCCESS, comments)
    """
    rundir   = case.get_value("RUNDIR")

    return _compare_hists(case, rundir, rundir, suffix1, suffix2,
                          ignore_fieldlist_diffs=ignore_fieldlist_diffs)

def cprnc(model, file1, file2, case, rundir, multiinst_driver_compare=False, outfile_suffix="",
          ignore_fieldlist_diffs=False):
    """
    Run cprnc to compare two individual nc files

    file1 - the full or relative path of the first file
    file2 - the full or relative path of the second file
    case - the case containing the files
    rundir - the rundir for the case
    outfile_suffix - if non-blank, then the output file name ends with this
        suffix (with a '.' added before the given suffix).
        Use None to avoid permissions issues in the case dir.
    ignore_fieldlist_diffs (bool): If True, then: If the two cases differ only in their
        field lists (i.e., all shared fields are bit-for-bit, but one case has some
        diagnostic fields that are missing from the other case), treat the two cases as
        identical.

    returns (True if the files matched, log_name, comment)
        where 'comment' is either an empty string or one of the module-level constants
        beginning with CPRNC_ (e.g., CPRNC_FIELDLISTS_DIFFER)
    """
    cprnc_exe = case.get_value("CCSM_CPRNC")
    basename = os.path.basename(file1)
    multiinst_regex = re.compile(r'.*%s[^_]*(_[0-9]{4})[.]h.?[.][^.]+?[.]nc' % model)
    mstr = ''
    mstr1 = ''
    mstr2 = ''
    #  If one is a multiinstance file but the other is not add an instance string
    m1 = multiinst_regex.match(file1)
    m2 = multiinst_regex.match(file2)
    if m1 is not None:
        mstr1 = m1.group(1)
    if m2 is not None:
        mstr2 = m2.group(1)
    if mstr1 != mstr2:
        mstr = mstr1+mstr2

    output_filename = os.path.join(rundir, "{}{}.cprnc.out".format(basename, mstr))
    if outfile_suffix:
        output_filename += ".{}".format(outfile_suffix)

    if outfile_suffix is None:
        cpr_stat, out, _ = run_cmd("{} -m {} {}".format(cprnc_exe, file1, file2), combine_output=True)
    else:
        cpr_stat = run_cmd("{} -m {} {}".format(cprnc_exe, file1, file2), combine_output=True, arg_stdout=output_filename)[0]
        with open(output_filename, "r") as fd:
            out = fd.read()

    comment = ''
    if cpr_stat == 0:
        # Successful exit from cprnc
        if multiinst_driver_compare:
            #  In a multiinstance test the cpl hist file will have a different number of
            # dimensions and so cprnc will indicate that the files seem to be DIFFERENT
            # in this case we only want to check that the fields we are able to compare
            # have no differences.
            files_match = " 0 had non-zero differences" in out
        else:
            if "files seem to be IDENTICAL" in out:
                files_match = True
            elif "the two files seem to be DIFFERENT" in out:
                files_match = False
            elif "the two files DIFFER only in their field lists" in out:
                if ignore_fieldlist_diffs:
                    files_match = True
                else:
                    files_match = False
                    comment = CPRNC_FIELDLISTS_DIFFER
            else:
                expect(False, "Did not find an expected summary string in cprnc output")
    else:
        # If there is an error in cprnc, we do the safe thing of saying the comparison failed
        files_match = False
    return (files_match, output_filename, comment)

def compare_baseline(case, baseline_dir=None, outfile_suffix=""):
    """
    compare the current test output to a baseline result

    case - The case containing the hist files to be compared against baselines
    baseline_dir - Optionally, specify a specific baseline dir, otherwise it will be computed from case config
    outfile_suffix - if non-blank, then the cprnc output file name ends with
        this suffix (with a '.' added before the given suffix). if None, no output file saved.

    returns (SUCCESS, comments)
    SUCCESS means all hist files matched their corresponding baseline
    """
    rundir   = case.get_value("RUNDIR")
    if baseline_dir is None:
        baselineroot = case.get_value("BASELINE_ROOT")
        basecmp_dir = os.path.join(baselineroot, case.get_value("BASECMP_CASE"))
        dirs_to_check = (baselineroot, basecmp_dir)
    else:
        basecmp_dir = baseline_dir
        dirs_to_check = (basecmp_dir,)

    for bdir in dirs_to_check:
        if not os.path.isdir(bdir):
            return False, "ERROR {} baseline directory '{}' does not exist".format(TEST_NO_BASELINES_COMMENT,bdir)

    success, comments = _compare_hists(case, rundir, basecmp_dir, outfile_suffix=outfile_suffix)
    if get_model() == "e3sm":
        bless_log = os.path.join(basecmp_dir, BLESS_LOG_NAME)
        if os.path.exists(bless_log):
            lines = open(bless_log, "r").readlines()
            if lines:
                last_line = lines[-1]
                comments += "\n  Most recent bless: {}".format(last_line)

    return success, comments

def generate_teststatus(testdir, baseline_dir):
    """
    CESM stores it's TestStatus file in baselines. Do not let exceptions
    escape from this function.
    """
    if get_model() == "cesm":
        try:
            with SharedArea():
                if not os.path.isdir(baseline_dir):
                    os.makedirs(baseline_dir)

                safe_copy(os.path.join(testdir, TEST_STATUS_FILENAME), baseline_dir, preserve_meta=False)
        except Exception as e:
            logger.warning("Could not copy {} to baselines, {}".format(os.path.join(testdir, TEST_STATUS_FILENAME), str(e)))

def _generate_baseline_impl(case, baseline_dir=None, allow_baseline_overwrite=False):
    """
    copy the current test output to baseline result

    case - The case containing the hist files to be copied into baselines
    baseline_dir - Optionally, specify a specific baseline dir, otherwise it will be computed from case config
    allow_baseline_overwrite must be true to generate baselines to an existing directory.

    returns (SUCCESS, comments)
    """
    rundir   = case.get_value("RUNDIR")
    ref_case = case.get_value("RUN_REFCASE")
    if baseline_dir is None:
        baselineroot = case.get_value("BASELINE_ROOT")
        basegen_dir = os.path.join(baselineroot, case.get_value("BASEGEN_CASE"))
    else:
        basegen_dir = baseline_dir
    testcase = case.get_value("CASE")
    archive = case.get_env('archive')

    if not os.path.isdir(basegen_dir):
        os.makedirs(basegen_dir)

    if (os.path.isdir(os.path.join(basegen_dir,testcase)) and
        not allow_baseline_overwrite):
        expect(False, " Cowardly refusing to overwrite existing baseline directory")

    comments = "Generating baselines into '{}'\n".format(basegen_dir)
    num_gen = 0
    for model in _iter_model_file_substrs(case):
        comments += "  generating for model '{}'\n".format(model)

        hists =  archive.get_latest_hist_files(testcase, model, rundir, ref_case=ref_case)
        logger.debug("latest_files: {}".format(hists))
        num_gen += len(hists)
        for hist in hists:
            offset = hist.rfind(model)
            expect(offset >= 0,"ERROR: cant find model name {} in {}".format(model, hist))
            baseline = os.path.join(basegen_dir, hist[offset:])
            if os.path.exists(baseline):
                os.remove(baseline)

            safe_copy(os.path.join(rundir,hist), baseline, preserve_meta=False)
            comments += "    generating baseline '{}' from file {}\n".format(baseline, hist)

    # copy latest cpl log to baseline
    # drop the date so that the name is generic
    if case.get_value("COMP_INTERFACE") == "nuopc":
        cplname = "med"
    else:
        cplname = "cpl"

    newestcpllogfile = case.get_latest_cpl_log(coupler_log_path=case.get_value("RUNDIR"), cplname=cplname)
    if newestcpllogfile is None:
        logger.warning("No {}.log file found in directory {}".format(cplname,case.get_value("RUNDIR")))
    else:
        safe_copy(newestcpllogfile, os.path.join(basegen_dir, "{}.log.gz".format(cplname)), preserve_meta=False)

    testname = case.get_value("TESTCASE")
    testopts = parse_test_name(case.get_value("CASEBASEID"))[1]
    testopts = [] if testopts is None else testopts
    expect(num_gen > 0 or (testname in ["PFS", "TSC"] or "B" in testopts),
           "Could not generate any hist files for case '{}', something is seriously wrong".format(os.path.join(rundir, testcase)))

    if get_model() == "e3sm":
        bless_log = os.path.join(basegen_dir, BLESS_LOG_NAME)
        with open(bless_log, "a") as fd:
            fd.write("sha:{} date:{}\n".format(get_current_commit(repo=case.get_value("CIMEROOT")),
                                               get_timestamp(timestamp_format="%Y-%m-%d_%H:%M:%S")))

    return True, comments

def generate_baseline(case, baseline_dir=None, allow_baseline_overwrite=False):
    with SharedArea():
        return _generate_baseline_impl(case, baseline_dir=baseline_dir, allow_baseline_overwrite=allow_baseline_overwrite)

def get_ts_synopsis(comments):
    r"""
    Reduce case diff comments down to a single line synopsis so that we can put
    something in the TestStatus file. It's expected that the comments provided
    to this function came from compare_baseline, not compare_tests.

    >>> get_ts_synopsis('')
    ''
    >>> get_ts_synopsis('big error')
    'big error'
    >>> get_ts_synopsis('big error\n')
    'big error'
    >>> get_ts_synopsis('stuff\n    File foo had a different field list from bar with suffix baz\nPass\n')
    'FIELDLIST field lists differ (otherwise bit-for-bit)'
    >>> get_ts_synopsis('stuff\n    File foo had no compare counterpart in bar with suffix baz\nPass\n')
    'ERROR BFAIL some baseline files were missing'
    >>> get_ts_synopsis('stuff\n    File foo had a different field list from bar with suffix baz\n    File foo had no compare counterpart in bar with suffix baz\nPass\n')
    'MULTIPLE ISSUES: field lists differ and some baseline files were missing'
    >>> get_ts_synopsis('stuff\n    File foo did NOT match bar with suffix baz\nPass\n')
    'DIFF'
    >>> get_ts_synopsis('stuff\n    File foo did NOT match bar with suffix baz\n    File foo had a different field list from bar with suffix baz\nPass\n')
    'DIFF'
    >>> get_ts_synopsis('stuff\n    File foo did NOT match bar with suffix baz\n    File foo had no compare counterpart in bar with suffix baz\nPass\n')
    'DIFF'
    >>> get_ts_synopsis('File foo had no compare counterpart in bar with suffix baz\n File foo had no original counterpart in bar with suffix baz\n')
    'DIFF'
    """
    if not comments:
        return ""
    elif "\n" not in comments.strip():
        return comments.strip()
    else:
        has_fieldlist_differences = False
        has_bfails = False
        has_real_fails = False
        for line in comments.splitlines():
            if FIELDLISTS_DIFFER in line:
                has_fieldlist_differences = True
            if NO_COMPARE in line:
                has_bfails = True
            for comparison_failure_comment in COMPARISON_FAILURE_COMMENT_OPTIONS:
                if comparison_failure_comment in line:
                    has_real_fails = True

        if has_real_fails:
            # If there are any real differences, we just report that: we assume that the
            # user cares much more about those real differences than fieldlist or bfail
            # issues, and we don't want to complicate the matter by trying to report all
            # issues in this case.
            return "DIFF"
        else:
            if has_fieldlist_differences and has_bfails:
                # It's not clear which of these (if either) the user would care more
                # about, so we report both. We deliberately avoid printing the keywords
                # 'FIELDLIST' or TEST_NO_BASELINES_COMMENT (i.e., 'BFAIL'): if we printed
                # those, then (e.g.) a 'grep -v FIELDLIST' (which the user might do if
                # (s)he was expecting fieldlist differences) would also filter out this
                # line, which we don't want.
                return "MULTIPLE ISSUES: field lists differ and some baseline files were missing"
            elif has_fieldlist_differences:
                return "FIELDLIST field lists differ (otherwise bit-for-bit)"
            elif has_bfails:
                return "ERROR {} some baseline files were missing".format(TEST_NO_BASELINES_COMMENT)
            else:
                return ""
