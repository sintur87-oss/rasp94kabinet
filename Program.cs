using System;
using System.Collections.Generic;
using System.Text;

internal sealed class Program
{
    private sealed class DisjointSetUnion
    {
        private readonly int[] _parent;
        private readonly int[] _size;

        public DisjointSetUnion(int n)
        {
            _parent = new int[n];
            _size = new int[n];
            for (int i = 0; i < n; i++)
            {
                _parent[i] = i;
                _size[i] = 1;
            }
        }

        public int Find(int x)
        {
            if (_parent[x] != x)
            {
                _parent[x] = Find(_parent[x]);
            }

            return _parent[x];
        }

        public void Union(int a, int b)
        {
            int ra = Find(a);
            int rb = Find(b);
            if (ra == rb)
            {
                return;
            }

            if (_size[ra] < _size[rb])
            {
                (ra, rb) = (rb, ra);
            }

            _parent[rb] = ra;
            _size[ra] += _size[rb];
        }
    }

    private sealed class GroupResult
    {
        public string Representative { get; }
        public int Frequency { get; }

        public GroupResult(string representative, int frequency)
        {
            Representative = representative;
            Frequency = frequency;
        }
    }

    private static void Main()
    {
        string? firstLine = Console.ReadLine();
        if (string.IsNullOrWhiteSpace(firstLine))
        {
            return;
        }

        int k = int.Parse(firstLine.Trim());

        var tokenSequence = new List<string>(capacity: 100_000);
        var uniqueWords = new List<string>();
        var wordToId = new Dictionary<string, int>(StringComparer.Ordinal);

        while (true)
        {
            string? line = Console.ReadLine();
            if (line == null || line.Length == 0)
            {
                break;
            }

            string[] parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            foreach (string part in parts)
            {
                string normalized = Normalize(part);
                if (normalized.Length == 0)
                {
                    continue;
                }

                tokenSequence.Add(normalized);
                if (!wordToId.ContainsKey(normalized))
                {
                    wordToId[normalized] = uniqueWords.Count;
                    uniqueWords.Add(normalized);
                }
            }
        }

        int uniqueCount = uniqueWords.Count;
        var dsu = new DisjointSetUnion(uniqueCount);

        BuildSameLengthEdges(uniqueWords, wordToId, dsu);
        BuildSuffixEdges(uniqueWords, wordToId, dsu);

        int n = tokenSequence.Count;
        var tokenIds = new int[n];
        var tokenRoots = new int[n];
        for (int i = 0; i < n; i++)
        {
            tokenIds[i] = wordToId[tokenSequence[i]];
            tokenRoots[i] = dsu.Find(tokenIds[i]);
        }

        var groupFrequency = new Dictionary<int, int>();
        for (int i = 0; i < n; i++)
        {
            int currentRoot = tokenRoots[i];
            bool hasNeighborInSameGroup = false;

            int left = Math.Max(0, i - k);
            int right = Math.Min(n - 1, i + k);
            for (int j = left; j <= right; j++)
            {
                if (j == i)
                {
                    continue;
                }

                if (tokenRoots[j] == currentRoot)
                {
                    hasNeighborInSameGroup = true;
                    break;
                }
            }

            if (hasNeighborInSameGroup)
            {
                if (!groupFrequency.ContainsKey(currentRoot))
                {
                    groupFrequency[currentRoot] = 0;
                }

                groupFrequency[currentRoot]++;
            }
        }

        var representativeByRoot = new Dictionary<int, string>();
        for (int id = 0; id < uniqueCount; id++)
        {
            int root = dsu.Find(id);
            string word = uniqueWords[id];

            if (!representativeByRoot.TryGetValue(root, out string? currentRepresentative) ||
                string.CompareOrdinal(word, currentRepresentative) < 0)
            {
                representativeByRoot[root] = word;
            }
        }

        var output = new List<GroupResult>();
        foreach (var pair in groupFrequency)
        {
            int root = pair.Key;
            int frequency = pair.Value;
            if (frequency <= 0)
            {
                continue;
            }

            output.Add(new GroupResult(representativeByRoot[root], frequency));
        }

        output.Sort((a, b) =>
        {
            int frequencyCompare = b.Frequency.CompareTo(a.Frequency);
            if (frequencyCompare != 0)
            {
                return frequencyCompare;
            }

            return string.CompareOrdinal(a.Representative, b.Representative);
        });

        foreach (GroupResult item in output)
        {
            Console.WriteLine($"{item.Representative}: {item.Frequency}");
        }
    }

    private static void BuildSameLengthEdges(IReadOnlyList<string> uniqueWords, IReadOnlyDictionary<string, int> wordToId, DisjointSetUnion dsu)
    {
        var patternToWordId = new Dictionary<string, int>(StringComparer.Ordinal);

        for (int id = 0; id < uniqueWords.Count; id++)
        {
            string word = uniqueWords[id];
            if (word.Length <= 1)
            {
                continue;
            }

            for (int pos = 0; pos < word.Length; pos++)
            {
                string pattern = BuildWildcardPattern(word, pos);
                if (patternToWordId.TryGetValue(pattern, out int existingId))
                {
                    dsu.Union(id, existingId);
                }
                else
                {
                    patternToWordId[pattern] = id;
                }
            }
        }
    }

    private static void BuildSuffixEdges(IReadOnlyList<string> uniqueWords, IReadOnlyDictionary<string, int> wordToId, DisjointSetUnion dsu)
    {
        for (int id = 0; id < uniqueWords.Count; id++)
        {
            string longer = uniqueWords[id];
            if (longer.Length <= 2)
            {
                continue;
            }

            char last = longer[longer.Length - 1];
            if (last != 'e' && last != 's')
            {
                continue;
            }

            string shorter = longer.Substring(0, longer.Length - 1);
            if (shorter.Length <= 1)
            {
                continue;
            }

            if (wordToId.TryGetValue(shorter, out int shorterId))
            {
                dsu.Union(id, shorterId);
            }
        }
    }

    private static string BuildWildcardPattern(string word, int position)
    {
        var sb = new StringBuilder(word.Length);
        for (int i = 0; i < word.Length; i++)
        {
            sb.Append(i == position ? '*' : word[i]);
        }

        return sb.ToString();
    }

    private static string Normalize(string token)
    {
        var sb = new StringBuilder(token.Length);
        foreach (char ch in token)
        {
            if ((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || ch == '\'')
            {
                sb.Append(char.ToLowerInvariant(ch));
            }
        }

        return sb.ToString();
    }
}
