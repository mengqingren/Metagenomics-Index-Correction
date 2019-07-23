#!/usr/bin/env python3
"""
This script parses a Centrifuge-generated file to give some stats about the classifications.
"""

import argparse
import collections
import gzip
import sys


def get_arguments():
    parser = argparse.ArgumentParser(description='Summarise classifications from Centrifuge SAM '
                                                 'files')
    parser.add_argument('--centrifuge', type=str, required=True,
                        help='a classifications file generated by Centrifuge (can be gzipped)')
    parser.add_argument('--tree', type=str, required=True,
                        help='a taxonomy tree file (can be gzipped)')
    parser.add_argument('--prefix', type=str, required=True,
                        help='prefix for output files')

    args = parser.parse_args()
    return args


def main():
    args = get_arguments()

    tax_id_to_parent, tax_id_to_rank, tax_id_to_standard_rank = load_tax_info(args.tree)
    tax_ids_per_read = load_tax_ids_per_read(args.centrifuge)
    read_count = len(tax_ids_per_read)

    count_per_rank = collections.defaultdict(int)
    cumulative_counts_per_tax_id = collections.defaultdict(int)

    read_table_filename = args.prefix + '_reads.tsv'
    with open(read_table_filename, 'wt') as read_file:
        read_file.write('\t'.join(['read_name', 'alignment_tax_ids', 'lca_tax_id', 'lca_rank',
                                   'lca_standard_rank']))
        read_file.write('\n')

        for read_name, tax_ids in tax_ids_per_read.items():
            tax_id = add_rank_count(read_name, count_per_rank, tax_ids, tax_id_to_rank,
                                    tax_id_to_standard_rank, tax_id_to_parent, read_file)
            if tax_id > 0:  # the read was classified
                while True:
                    cumulative_counts_per_tax_id[tax_id] += 1
                    if tax_id == 1:  # gotten to the root
                        break
                    tax_id = tax_id_to_parent[tax_id]

    write_summary(args.centrifuge, args.prefix, read_count, count_per_rank,
                  cumulative_counts_per_tax_id, tax_id_to_rank)
    write_cumulative_count_table(args.centrifuge, args.prefix, cumulative_counts_per_tax_id,
                                 read_count, tax_id_to_rank)


def load_tax_info(tree_filename):
    """
    This function reads through the tree file, and returns three dictionaries:
      1) Where the keys are tax IDs and the values are the parent tax IDs. This dictionary allows
         for tracing 'upward' (toward the root) through the tree, starting at any node.
      2) Where the keys are tax IDs and the values are taxonomic ranks. Importantly, this
         dictionary does not include all taxonomic ranks in the tree, but only the standard levels
         (phylum, class, order, etc). When a tax ID has a non-standard rank (e.g. subfamily), that
         ID is given the first standard rank found in its ancestors (e.g. family).
      3) Where the keys are tax IDs and the values are tax IDs for the first ancestor that has a
         standard taxonomic rank.
    """
    tree_data = []
    open_func = get_open_func(tree_filename)
    with open_func(tree_filename, 'rt') as tree_file:
        for line in tree_file:
            parts = line.strip().split('\t')
            tree_data.append([int(parts[0]), int(parts[2]), parts[4].lower()])

    tax_id_to_parent = {}
    for tax_id, parent_id, _ in tree_data:
        tax_id_to_parent[tax_id] = parent_id

    # The first time we go through the tax IDs, we save any which has an acceptable rank.
    tax_id_to_rank, tax_id_to_standard_rank = {0: 'unclassified'}, {0: 'unclassified'}
    acceptable_ranks = {'domain', 'phylum', 'class', 'order', 'family', 'genus', 'species'}
    for tax_id, parent_id, rank in tree_data:
        tax_id_to_rank[tax_id] = rank
        if rank in acceptable_ranks:
            tax_id_to_standard_rank[tax_id] = rank
        elif tax_id == 1:  # special case for the root node
            assert tax_id == parent_id  # the root is its own parent
            tax_id_to_standard_rank[tax_id] = 'root'
            tax_id_to_rank[tax_id] = 'root'

    # Now we go through a second time to deal with tax IDs that didn't get an acceptable rank the
    # first time.
    for tax_id, _, rank in tree_data:
        if tax_id in tax_id_to_standard_rank:
            continue
        assert rank not in acceptable_ranks
        ancestors = get_all_ancestors(tax_id, tax_id_to_parent)
        for ancestor in ancestors:
            if ancestor in tax_id_to_standard_rank:
                rank = tax_id_to_standard_rank[ancestor]
                tax_id_to_standard_rank[tax_id] = rank
                break
        assert tax_id in tax_id_to_standard_rank

    return tax_id_to_parent, tax_id_to_rank, tax_id_to_standard_rank


def load_tax_ids_per_read(classification_filename):
    """
    Returns a dictionary where the key is the read name and the value is a list of all tax IDs that
    read was classified to.
    """
    tax_ids_per_read = collections.OrderedDict()
    with get_open_func(classification_filename)(classification_filename, 'rt') as class_file:
        for line in class_file:
            parts = line.strip().split('\t')
            read_name = parts[0]
            if read_name == 'readID':  # header
                continue
            seq_id, tax_id = parts[1], int(parts[2])
            if seq_id == 'no rank':
                tax_id = 1
            if tax_id == 0:
                # A taxID of 0 with a non-unclassified seqID implies something went wrong in the
                # index building.
                assert seq_id == 'unclassified'
            if read_name not in tax_ids_per_read:
                tax_ids_per_read[read_name] = set()
            tax_ids_per_read[read_name].add(tax_id)
    return tax_ids_per_read


def add_rank_count(read_name, count_per_rank, tax_ids, tax_id_to_rank, tax_id_to_standard_rank,
                   tax_id_to_parent, read_file):
    if len(tax_ids) == 0:
        return
    tax_ids_str = ','.join(str(i) for i in tax_ids)
    read_file.write('{}\t{}\t'.format(read_name, tax_ids_str))
    if len(tax_ids) == 1:
        (tax_id,) = tax_ids
    else:
        tax_ids.discard(0)
        tax_id = find_lca(tax_ids, tax_id_to_parent)
    rank = tax_id_to_rank[tax_id]
    standard_rank = tax_id_to_standard_rank[tax_id]
    read_file.write('{}\t{}\t{}\n'.format(tax_id, rank, standard_rank))
    count_per_rank[standard_rank] += 1
    return tax_id


def find_lca(tax_ids, tax_id_to_parent):
    """
    This function takes a set of tax IDs and (using the tree structure in tax_id_to_parent) returns
    the tax ID of their lowest common ancestor.
    """
    # Find the set of ancestor taxa common to all of the input tax IDs.
    common_taxa = set()
    for tax_id in tax_ids:
        ancestors = get_all_ancestors(tax_id, tax_id_to_parent)
        if not common_taxa:
            common_taxa = set(ancestors)
        else:
            common_taxa &= set(ancestors)

    # Return the first ancestor that's in the common set.
    one_tax_id = next(iter(tax_ids))  # just get one of the tax IDs (doesn't matter which)
    for ancestor in get_all_ancestors(one_tax_id, tax_id_to_parent):
        if ancestor in common_taxa:
            return ancestor

    # The code should never get here! I.e. at least one of the tax ID's ancestors should be in the
    # common set.
    assert False


def get_all_ancestors(tax_id, tax_id_to_parent):
    """
    Given a tax ID, this function returns a list of all of its ancestors (including the tax ID
    itself). The list is ordered with the tax ID at the start and the root (tax ID 1) at the end.
    """
    ancestors = [tax_id]
    while tax_id != 1:  # loop until we hit the root
        tax_id = tax_id_to_parent[tax_id]
        ancestors.append(tax_id)
    assert ancestors[-1] == 1  # all ancestor lists should end with node 1 (the root)
    return ancestors


def write_summary(classification_filename, prefix, read_count, count_per_rank,
                  cumulative_counts_per_tax_id, tax_id_to_rank):
    ranks = ['unclassified', 'root', 'domain', 'phylum', 'class', 'order', 'family', 'genus',
             'species']
    taxa_count_ranks = ['domain', 'phylum', 'class', 'order', 'family', 'genus', 'species']
    rank_set = set(ranks)

    # Count the number of taxa in each of the standard ranks.
    rank_tax_ids = collections.defaultdict(set)
    for tax_id, count in cumulative_counts_per_tax_id.items():
        if count > 0:
            rank = tax_id_to_rank[tax_id]
            if rank in rank_set:
                rank_tax_ids[rank].add(tax_id)
    rank_counts = {rank: len(tax_ids) for rank, tax_ids in rank_tax_ids.items()}
    for rank in ranks:
        if rank not in rank_counts:
            rank_counts[rank] = 0

    summary_filename = prefix + '_summary.tsv'
    with open(summary_filename, 'wt') as summary_file:
        header = 'file\tread_count'

        for rank in ranks:
            header += '\t{}_read_count\t{}_read_percent'.format(rank, rank)
            if rank in taxa_count_ranks:
                header += '\t{}_taxa_count'.format(rank)
        summary_file.write(header)
        summary_file.write('\n')

        total = sum(count_per_rank.values())
        summary = '{}\t{}'.format(classification_filename, read_count)
        total_count = 0
        for rank in ranks:
            count = count_per_rank[rank]
            total_count += count
            percent = 100 * count / total
            summary += '\t{}\t{:.4f}'.format(count, percent)
            if rank in taxa_count_ranks:
                summary += '\t{}'.format(rank_counts[rank])
        assert read_count == total_count  # sanity check
        summary_file.write(summary)
        summary_file.write('\n')


def write_cumulative_count_table(classification_filename, prefix, cumulative_counts_per_tax_id,
                                 read_count, tax_id_to_rank):
    rank_ordering = ['root', 'domain', 'subdomain', 'hyperkingdom', 'superkingdom', 'kingdom',
                     'subkingdom', 'infrakingdom', 'parvkingdom', 'superphylum', 'phylum',
                     'subphylum', 'infraphylum', 'microphylum', 'superclass', 'class', 'subclass',
                     'infraclass', 'parvclass', 'superdivision', 'division', 'subdivision',
                     'infradivision', 'superlegion', 'legion', 'sublegion', 'infralegion',
                     'supercohort', 'cohort', 'subcohort', 'infracohort', 'gigaorder', 'magnorder',
                     'grandorder', 'mirorder', 'superorder', 'order', 'nanorder', 'hypoorder',
                     'minorder', 'suborder', 'infraorder', 'parvorder', 'microorder', 'gigafamily',
                     'megafamily', 'grandfamily', 'hyperfamily', 'superfamily', 'epifamily',
                     'family', 'subfamily', 'infrafamily', 'supertribe', 'tribe', 'subtribe',
                     'infratribe', 'genus', 'subgenus', 'section', 'subsection', 'series',
                     'subseries', 'species group', 'species subgroup', 'superspecies', 'species',
                     'subspecies', 'varietas', 'subvarietas', 'forma', 'subforma', 'strain',
                     'no rank']
    rank_ordering = {rank: n for n, rank in enumerate(rank_ordering)}

    tax_ids = list(cumulative_counts_per_tax_id.keys())
    tax_ids = sorted(tax_ids, key=lambda x: (rank_ordering[tax_id_to_rank[x]], x))

    count_filename = prefix + '_counts.tsv'
    with open(count_filename, 'wt') as count_file:
        count_file.write('read_set')
        for tax_id in tax_ids:
            count_file.write('\t{}-{}'.format(tax_id_to_rank[tax_id], tax_id))
        count_file.write('\n')
        count_file.write(classification_filename)
        for tax_id in tax_ids:
            count_file.write('\t{:.6f}'.format(cumulative_counts_per_tax_id[tax_id] / read_count))
        count_file.write('\n')


def get_compression_type(filename):
    """
    Attempts to guess the compression (if any) on a file using the first few bytes.
    http://stackoverflow.com/questions/13044562
    """
    magic_dict = {'gz': (b'\x1f', b'\x8b', b'\x08'),
                  'bz2': (b'\x42', b'\x5a', b'\x68'),
                  'zip': (b'\x50', b'\x4b', b'\x03', b'\x04')}
    max_len = max(len(x) for x in magic_dict)

    unknown_file = open(filename, 'rb')
    file_start = unknown_file.read(max_len)
    unknown_file.close()
    compression_type = 'plain'
    for file_type, magic_bytes in magic_dict.items():
        if file_start.startswith(magic_bytes):
            compression_type = file_type
    if compression_type == 'bz2':
        sys.exit('Error: cannot use bzip2 format - use gzip instead')
    if compression_type == 'zip':
        sys.exit('Error: cannot use zip format - use gzip instead')
    return compression_type


def get_open_func(filename):
    if get_compression_type(filename) == 'gz':
        return gzip.open
    else:  # plain text
        return open


if __name__ == '__main__':
    main()
