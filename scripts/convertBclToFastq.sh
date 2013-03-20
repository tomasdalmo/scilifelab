#! /bin/bash

###########################################################################################################
###                                                                                                     ###
### Script for converting and demultiplexing bcl files to fastq files, using the scripts in CASAVA 1.8+ ###
###                                                                                                     ###
### Requires that you have the relevant CASAVA scripts in your PATH, a sample sheet (by default assumed ###
### to be 'SampleSheet.csv' and located in the run directory) and the script should be run from the run ###
### folder.                                                                                             ###
###                                                                                                     ###
###########################################################################################################

set -e

INDIR="Data/Intensities/BaseCalls"
OUTDIR="Unaligned"
SSHEET="SampleSheet.csv"
MM=1
BASEMASK=""

# Parse optional command line arguments
while getopts ":i:m:o:s:b:h" opt; do
  case $opt in
    m)
      MM=${OPTARG}
      ;;
    i)
      INDIR=${OPTARG}
      ;;
    o)
      OUTDIR=${OPTARG}
      ;;
    s)
      SSHEET=${OPTARG}
      ;;
    b)
      BASEMASK=${OPTARG}
      ;;
    h)
      echo $"
Usage: $0 [-i INDIR -o OUTDIR -m MISMATCHES -s SAMPLESHEET -b BASEMASK]

    -i INDIR       Input directory, default is ${INDIR}
    -o OUTDIR      Output directory, default is ${OUTDIR}
    -s SAMPLESHEET Sample sheet, default is ${SSHEET} 
    -m MISMATCHES  Number of allowed mismatches, default is ${MM}
    -b BASEMASK    The base mask to use, default is to auto-detect
" >&2
      exit 0
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      exit 1
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      exit 1
      ;;
  esac
done

# Configure the conversion
CMD="
configureBclToFastq.pl \
--input-dir ${INDIR} \
--output-dir ${OUTDIR} \
--mismatches ${MM} \
--fastq-cluster-count 0 \
--sample-sheet ${SSHEET} \
"
if [ ! -z ${BASEMASK} ]
then
  CMD="${CMD} --use-bases-mask ${BASEMASK}"
fi

LOG="configureBclToFastq.log"
echo `date`$'\t'"Configuring the bcl to fastq conversion, log is ${LOG}"
${CMD} >& ${LOG}

echo `date`$'\t'"Changing to ${OUTDIR}"
cd ${OUTDIR}

LOG="../bclToFastq.log"
echo `date`$'\t'"Running bcl conversion, log is ${LOG}"

CMD="
make \
-j ${J}"

${CMD} >& ${LOG}
cd ..

echo `date`$'\t'"Done with bcl conversion and demultiplexing"


